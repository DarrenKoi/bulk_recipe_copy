from datetime import datetime

import pytest

from backend import create_app, layout
from backend.fake_fleet import KST, FakeTool
from backend.jobs import run_until_idle
from backend.roster import Tool
from backend.scheduler import Entry, admit

EQPS = ["S1", "S2", "S3", "T1", "T2", "T3", "T4", "T5"]
ROSTER = [Tool(e, f"192.0.2.{i}", "M16A", "CG6300", "HITACHI") for i, e in enumerate(EQPS, start=1)]
T = datetime(2026, 10, 18, 9, 30, tzinfo=KST)
IDP = layout.idp_path("ABC_GATE", "W1", "P1")


def e(job_id, tools, started=False):
    return Entry(job_id, frozenset(tools.split()), len(tools.split()) - 1, started)


def test_admission_rules_on_literal_queues():
    # 겹침: 원본이 같은 뒤 job 은 기다리고, 겹치지 않는 job 은 같이 시작합니다.
    assert admit([e(1, "S1 T1"), e(2, "S1 T2"), e(3, "S2 T3")], cap=4) == ([1, 3], [])
    # 대기 중인 앞선 job 과 겹쳐도 기다립니다(2가 1을 기다리는 동안 3은 2와 T2가 겹침).
    assert admit([e(1, "S1 T1", True), e(2, "S1 T2"), e(3, "S2 T2")], cap=4) == ([], [])
    # 예산: 2는 슬롯이 모자라 기다리고, 3은 들어가도 앞지르지 않습니다.
    assert admit([e(1, "S1 T1 T2", True), e(2, "S2 T3 T4"), e(3, "S3 T5")], cap=3) == ([], [])
    assert admit([e(2, "S2 T3 T4"), e(3, "S3 T5")], cap=3) == ([2, 3], [])
    # 장비 겹침으로 기다리는 job 은 예산 대기가 아니라 뒤 job 을 막지 않습니다.
    assert admit([e(1, "S1 T1", True), e(2, "S1 T2"), e(3, "S2 T3")], cap=2) == ([3], [])
    # 상한을 넘는 job 은 끝내고, 큐 앞을 막지 않습니다.
    assert admit([e(1, "S1 T1 T2 T3"), e(2, "S1 T4")], cap=2) == ([2], [1])


@pytest.fixture
def fleet():
    tools = {eqp: FakeTool() for eqp in EQPS}
    for eqp, tool in tools.items():
        tool.dirs.add(layout.ROOT)
        if eqp.startswith("S"):
            tool.add_recipe("ABC_GATE", "W1", "P1", {"a.tif": eqp.encode()}, T)
    return tools


@pytest.fixture
def make(tmp_path, fleet):
    def make(**settings):
        app = create_app(
            roster=lambda: ROSTER,
            connect=lambda tool: fleet[tool.eqp_id],
            db_path=tmp_path / "brc.sqlite3",
            snapshot_dir=tmp_path / "snapshots",
            allowed_users={"engineer1"},
            spa_dir=tmp_path / "no-spa",
            scheduler=False,
            **settings,
        )
        c = app.test_client()
        c.set_cookie("LASTUSER", "engineer1")
        return app, c

    return make


def post(c, source, *targets) -> int:
    resp = c.post("/api/jobs", json={
        "source_eqp": source, "class_name": "ABC_GATE",
        "recipes": [{"idw": "W1", "idp": "P1"}], "targets": list(targets),
    })
    assert resp.status_code == 201, resp.json
    return resp.json["id"]


def board(c) -> dict:
    """job id -> (수집 상태, 대기 순번)."""
    return {j["id"]: (j["collection"]["state"], j["queue_position"]) for j in c.get("/api/jobs").json["jobs"]}


def finish(app, claimed) -> None:
    for job in claimed:
        app.config["SCHEDULER"].run(job, parallel=False)


def test_a_job_sharing_a_tool_with_an_earlier_one_waits_for_it(make, fleet):
    app, c = make()
    first, second = post(c, "S1", "T1"), post(c, "S2", "T1")
    assert board(c) == {first: ("waiting", 1), second: ("waiting", 2)}

    claimed = app.config["SCHEDULER"].admit()
    assert [j["id"] for j in claimed] == [first]
    assert board(c) == {first: ("collecting", None), second: ("waiting", 1)}
    assert app.config["SCHEDULER"].admit() == []

    finish(app, claimed)
    assert [j["id"] for j in app.config["SCHEDULER"].admit()] == [second]


def test_jobs_on_separate_tools_start_together_and_both_copy(make, fleet):
    app, c = make()
    first, second = post(c, "S1", "T1", "T2"), post(c, "S2", "T3")
    claimed = app.config["SCHEDULER"].admit()
    assert [j["id"] for j in claimed] == [first, second]
    assert board(c) == {first: ("collecting", None), second: ("collecting", None)}

    finish(app, claimed)
    assert [fleet[t].files[f"{layout.raw_dir('ABC_GATE', 'W1', 'P1')}/a.tif"][0] for t in ("T1", "T2", "T3")] == [
        b"S1", b"S1", b"S2"]
    assert IDP in fleet["T3"].files


def test_a_job_waiting_for_slots_is_not_overtaken_by_a_smaller_one(make):
    app, c = make(max_threads=3)
    big = post(c, "S1", "T1", "T2")
    waits = post(c, "S2", "T3", "T4")
    small = post(c, "S3", "T5")

    claimed = app.config["SCHEDULER"].admit()
    assert [j["id"] for j in claimed] == [big]
    assert board(c) == {big: ("collecting", None), waits: ("waiting", 1), small: ("waiting", 2)}

    finish(app, claimed)
    assert [j["id"] for j in app.config["SCHEDULER"].admit()] == [waits, small]


def test_a_job_over_a_lowered_cap_ends_with_a_reason_and_does_not_block_the_queue(make):
    _, c = make(max_threads=2)
    over, after = post(c, "S1", "T1", "T2"), post(c, "S1", "T3")

    app, c = make(max_threads=1)  # 재기동하며 상한을 줄임
    run_until_idle(app)
    jobs = {j["id"]: j for j in c.get("/api/jobs").json["jobs"]}
    assert jobs[over]["collection"] == {"state": "blocked", "reason": "상한 초과: 대상 2대 > 스레드 상한 1"}
    assert jobs[over]["file_counts"]["pending"] == 0
    assert (jobs[after]["collection"]["state"], jobs[after]["file_counts"]["verified"]) == ("done", 2)


def test_finished_jobs_hold_no_tools_even_with_uncertain_files(make, fleet):
    app, c = make()
    fleet["T1"].lose_rename_reply.add(IDP)
    first = post(c, "S1", "T1")
    run_until_idle(app)
    assert c.get(f"/api/jobs/{first}").json["file_counts"]["uncertain"] == 1

    second = post(c, "S1", "T1")
    assert [j["id"] for j in app.config["SCHEDULER"].admit()] == [second]
