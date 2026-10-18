import threading
from datetime import datetime

import pytest

from backend import create_app, layout, store
from backend.fake_fleet import KST, FakeTool
from backend.jobs import run_until_idle, snapshot_bytes
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
    # 대상에 class 폴더가 없으므로 폴더 생성을 허락한 확인입니다.
    resp = c.post("/api/jobs", json={
        "source_eqp": source, "class_name": "ABC_GATE",
        "recipes": [{"idw": "W1", "idp": "P1"}], "targets": list(targets),
        "create_class": list(targets),
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


def fail_start_of(monkeypatch, name: str) -> None:
    """이름이 `name` 인 스레드만 시작에 실패하게 합니다(스레드를 더 못 만드는 상황 흉내)."""
    real = threading.Thread.start

    def start(self):
        if self.name == name:
            raise RuntimeError("can't start new thread")
        real(self)

    monkeypatch.setattr(threading.Thread, "start", start)


def test_a_failed_target_start_keeps_the_job_holding_its_tools_until_started_workers_end(make, fleet, monkeypatch):
    app, c = make()
    sched = app.config["SCHEDULER"]
    first = post(c, "S1", "T1", "T2")
    fail_start_of(monkeypatch, f"brc-{first}-T2")
    in_t1, release = threading.Event(), threading.Event()
    fleet["T1"].on_download = lambda path: (in_t1.set(), release.wait(5))

    [job] = sched.admit()
    runner = threading.Thread(target=sched.run, args=(job,), kwargs={"parallel": True})
    runner.start()
    assert in_t1.wait(5)
    later = post(c, "S2", "T1")
    assert sched.admit() == []  # T1 작업자가 아직 살아 있습니다.

    release.set()
    runner.join(5)
    files = c.get(f"/api/jobs/{first}").json
    assert (files["file_counts"]["verified"], files["file_counts"]["failed"]) == (2, 2)
    assert [j["id"] for j in sched.admit()] == [later]


def test_concurrent_collections_share_one_snapshot_cap(make, fleet):
    # 스냅샷 하나는 9 B(`IDP P1\n` 7 B + `a.tif` 2 B). 상한 10 B 에는 하나만 듭니다.
    app, c = make(snapshot_cap_bytes=10)
    first, second = post(c, "S1", "T1"), post(c, "S2", "T2")
    both = threading.Barrier(2)

    def sync(path):  # 두 수집이 같이 돈다면 첫 다운로드 전에 서로 만납니다.
        try:
            both.wait(timeout=1)
        except threading.BrokenBarrierError:
            pass

    fleet["S1"].on_download = fleet["S2"].on_download = sync
    # 전송이 실패해야 스냅샷이 남습니다(전부 verified 면 바로 지워짐).
    fleet["T1"].reachable = fleet["T2"].reachable = False
    claimed = app.config["SCHEDULER"].admit()
    assert [j["id"] for j in claimed] == [first, second]
    runners = [threading.Thread(target=app.config["SCHEDULER"].run, args=(j,), kwargs={"parallel": False})
               for j in claimed]
    for t in runners:
        t.start()
    for t in runners:
        t.join(10)

    assert snapshot_bytes(app.config["SNAPSHOT_DIR"]) == 9
    states = sorted(c.get(f"/api/jobs/{j}").json["collection"]["state"] for j in (first, second))
    assert states == ["blocked", "done"]


def test_a_failed_claim_leaves_earlier_claims_running_and_later_jobs_waiting(make, monkeypatch):
    app, c = make()
    first, second = post(c, "S1", "T1"), post(c, "S2", "T2")
    real = store.claim_job

    def claim(db, job_id):
        if job_id == second:
            raise RuntimeError("database is locked")
        return real(db, job_id)

    monkeypatch.setattr(store, "claim_job", claim)
    claimed = app.config["SCHEDULER"].admit()
    assert [j["id"] for j in claimed] == [first]
    assert board(c) == {first: ("collecting", None), second: ("waiting", 1)}

    monkeypatch.setattr(store, "claim_job", real)
    finish(app, claimed)
    assert [j["id"] for j in app.config["SCHEDULER"].admit()] == [second]


def test_a_job_whose_thread_cannot_start_is_blocked_and_frees_its_tools(make, monkeypatch):
    app, c = make()
    first, later = post(c, "S1", "T1"), post(c, "S1", "T2")
    fail_start_of(monkeypatch, f"brc-job-{first}")

    app.config["SCHEDULER"].spawn()
    assert c.get(f"/api/jobs/{first}").json["collection"] == {"state": "blocked", "reason": "작업자 시작 실패"}
    assert [j["id"] for j in app.config["SCHEDULER"].admit()] == [later]


def test_a_failed_block_record_does_not_stop_later_jobs_from_starting(make, monkeypatch):
    import sqlite3

    import backend.jobs as jobs

    app, c = make()
    first, second = post(c, "S1", "T1"), post(c, "S2", "T2")
    fail_start_of(monkeypatch, f"brc-job-{first}")

    def locked(*_):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(jobs, "_block", locked)
    app.config["SCHEDULER"].spawn()
    for t in threading.enumerate():
        if t.name == f"brc-job-{second}":
            t.join(timeout=10)
    assert c.get(f"/api/jobs/{second}").json["collection"]["state"] == "done"
    assert first not in app.config["SCHEDULER"].running


def cancel_after_reading(monkeypatch, c, job_id):
    """대기 job 을 읽은 직후 그 job 이 취소되는 경쟁."""
    waiting_jobs = store.waiting_jobs

    def racing(db):
        jobs = waiting_jobs(db)
        assert c.post(f"/api/jobs/{job_id}/cancel").status_code == 200
        return jobs

    monkeypatch.setattr(store, "waiting_jobs", racing)


def test_a_job_cancelled_between_reading_and_claiming_is_skipped(make, monkeypatch):
    app, c = make()
    first, second = post(c, "S1", "T1"), post(c, "S2", "T3")
    cancel_after_reading(monkeypatch, c, first)
    assert [j["id"] for j in app.config["SCHEDULER"].admit()] == [second]
    assert board(c)[first] == ("blocked", None)


def test_an_over_cap_job_cancelled_between_reading_and_blocking_keeps_its_cancel_reason(make, monkeypatch):
    _, c = make(max_threads=2)
    over, ok = post(c, "S1", "T1", "T2"), post(c, "S2", "T3")
    app, c = make(max_threads=1)  # 재기동하며 상한을 줄임
    cancel_after_reading(monkeypatch, c, over)
    assert [j["id"] for j in app.config["SCHEDULER"].admit()] == [ok]
    assert c.get(f"/api/jobs/{over}").json["collection"] == {"state": "blocked", "reason": "취소"}
