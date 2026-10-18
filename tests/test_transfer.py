import os
from datetime import datetime

import pytest
from pyftpdlib.handlers import FTPHandler
from test_ftp_client import DropOnRenameHandler, _Server

from backend import create_app, layout, store
from backend.fake_fleet import KST, FakeTool
from backend.jobs import collect, run_until_idle
from backend.roster import Tool
from backend.transfer import transfer_job

ROSTER = [
    Tool("6MCDE301", "192.0.2.11", "M16A", "CG6300", "HITACHI"),
    Tool("6MCDE305", "192.0.2.12", "M16A", "CG6380", "HITACHI"),
    Tool("4CCD6701", "192.0.2.21", "M14", "CG5000", "HITACHI"),
]
T = datetime(2026, 10, 18, 9, 30, tzinfo=KST)
P1_RAW = {"a.tif": b"12345", "sub/b.csv": b"xyz"}
P1_IDP = layout.idp_path("ABC_GATE", "W1", "P1")
P1_RAW_DIR = layout.raw_dir("ABC_GATE", "W1", "P1")
A_TIF = f"{P1_RAW_DIR}/a.tif"


def hitachi() -> FakeTool:
    tool = FakeTool()
    tool.dirs.add(layout.ROOT)
    return tool


@pytest.fixture
def fleet():
    src = hitachi()
    src.add_recipe("ABC_GATE", "W1", "P1", P1_RAW, T)
    src.add_recipe("ABC_GATE", "W1", "P2", {"c.tif": b"1"}, T)
    src.add(layout.idw_path("ABC_GATE", "W1"), b"IDW", T)
    return {"6MCDE301": src, "6MCDE305": hitachi(), "4CCD6701": hitachi()}


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


def start(c, targets=("6MCDE305", "4CCD6701"), **choices) -> int:
    # 대상에 class 폴더가 없으므로 기본은 폴더 생성을 허락한 확인입니다.
    resp = c.post("/api/jobs", json={
        "source_eqp": "6MCDE301",
        "class_name": "ABC_GATE",
        "recipes": [{"idw": "W1", "idp": "P1"}, {"idw": "W1", "idp": "P2"}],
        "targets": list(targets),
        "create_class": list(targets),
    } | choices)
    assert resp.status_code == 201
    return resp.json["id"]


def recipe_files(tool: FakeTool) -> dict[str, bytes]:
    return {p: data for p, (data, _) in tool.files.items()}


def source_recipe_files(fleet) -> dict[str, bytes]:
    return {p: d for p, d in recipe_files(fleet["6MCDE301"]).items() if not p.endswith(".idw")}


def counts(**nonzero) -> dict:
    return {"pending": 0, "in_progress": 0, "verified": 0, "failed": 0, "uncertain": 0} | nonzero


def test_every_target_ends_with_the_source_recipes_byte_for_byte(make, fleet):
    app, c = make()
    job_id = start(c)
    run_until_idle(app)

    job = c.get(f"/api/jobs/{job_id}").json
    assert job["file_counts"] == counts(verified=10)
    assert job["problems"] == []
    expected = source_recipe_files(fleet)
    assert recipe_files(fleet["6MCDE305"]) == expected
    assert recipe_files(fleet["4CCD6701"]) == expected
    assert P1_RAW_DIR + "/sub" in fleet["6MCDE305"]._all_dirs()


def test_a_re_download_that_differs_is_a_hash_mismatch(make, fleet):
    fleet["6MCDE305"].corrupt_downloads.add(A_TIF)
    app, c = make()
    job_id = start(c)
    run_until_idle(app)

    job = c.get(f"/api/jobs/{job_id}").json
    assert job["file_counts"] == counts(verified=9, failed=1)
    assert job["problems"] == [
        {"eqp": "6MCDE305", "path": A_TIF, "state": "failed", "reason": "해시 불일치", "temp_path": None}
    ]


def test_a_damaged_snapshot_writes_nothing_and_fails_the_rest_of_the_job(make, fleet, tmp_path):
    # 같은 크기로 바뀐 스냅샷 + 대상에 이미 있는 정상 파일. 어느 것도 대상에 닿지 않아야 합니다.
    fleet["6MCDE305"].add(layout.idw_path("ABC_GATE", "W1"), b"IDW", T)
    before = {e: recipe_files(t) for e, t in fleet.items()}
    app, c = make()
    job_id = start(c)
    collect(app, store.claim_next_waiting(app.config["DB_PATH"]))
    (tmp_path / "snapshots" / str(job_id) / P1_IDP.lstrip("/")).write_bytes(b"IDP P9\n")

    transfer_job(app, job_id)

    job = c.get(f"/api/jobs/{job_id}").json
    assert job["file_counts"] == counts(failed=10)
    assert {p["reason"] for p in job["problems"]} == {"스냅샷 손상"}
    assert {e: recipe_files(t) for e, t in fleet.items()} == before
    assert fleet["4CCD6701"]._all_dirs() == {"/", "/HITACHI", "/HITACHI/DEVICE", layout.ROOT}


def test_a_raw_folder_that_appeared_after_confirmation_fails_that_pair_without_writing(make, fleet):
    app, c = make()
    job_id = start(c)
    fleet["6MCDE305"].dirs.add(P1_RAW_DIR)  # 확인 뒤, 첫 쓰기 전에 누군가 만든 빈 raw 폴더
    run_until_idle(app)

    job = c.get(f"/api/jobs/{job_id}").json
    assert job["file_counts"] == counts(verified=7, failed=3)
    assert sorted((p["eqp"], p["path"], p["reason"]) for p in job["problems"]) == [
        ("6MCDE305", P1_IDP, "확인 뒤 대상에 생김"),
        ("6MCDE305", A_TIF, "확인 뒤 대상에 생김"),
        ("6MCDE305", f"{P1_RAW_DIR}/sub/b.csv", "확인 뒤 대상에 생김"),
    ]
    written = recipe_files(fleet["6MCDE305"])
    assert not any(p == P1_IDP or p.startswith(P1_RAW_DIR + "/") for p in written)
    assert written == {p: d for p, d in source_recipe_files(fleet).items() if "/P2" in p}
    assert recipe_files(fleet["4CCD6701"]) == source_recipe_files(fleet)


def test_a_lost_rename_reply_is_uncertain_with_its_temp_path_and_the_copy_goes_on(make, fleet):
    # 가짜 장비는 FtpClient 처럼 그 연결을 버립니다. 뒤의 파일들은 새 연결로 verified 여야 합니다.
    fleet["6MCDE305"].lose_rename_reply.add(A_TIF)
    app, c = make()
    job_id = start(c, targets=["6MCDE305"])
    run_until_idle(app)

    job = c.get(f"/api/jobs/{job_id}").json
    assert job["file_counts"] == counts(verified=4, uncertain=1)
    assert job["problems"] == [{
        "eqp": "6MCDE305", "path": A_TIF, "state": "uncertain", "reason": "rename 응답 유실",
        "temp_path": f"{A_TIF}.0a1b2c.part",
    }]
    assert recipe_files(fleet["6MCDE305"]) == source_recipe_files(fleet)


@pytest.mark.parametrize("drop_after", range(12))
def test_a_dropped_connection_reconnects_and_resumes_from_the_same_file(make, fleet, drop_after):
    # drop_after 를 훑어 recheck·mkdir·put·재다운로드 각각의 직후에 끊기는 경우를 모두 지납니다.
    fleet["6MCDE305"].drop_after, fleet["6MCDE305"].drops = drop_after, 1
    app, c = make()
    job_id = start(c, targets=["6MCDE305"])
    run_until_idle(app)

    assert c.get(f"/api/jobs/{job_id}").json["file_counts"] == counts(verified=5)
    assert recipe_files(fleet["6MCDE305"]) == source_recipe_files(fleet)
    assert fleet["6MCDE305"].drops == 0


def test_exhausted_retries_fail_the_rest_of_that_target_only(make, fleet):
    fleet["6MCDE305"].drop_after, fleet["6MCDE305"].drops = 0, 99
    app, c = make(transfer_retries=2)
    job_id = start(c)
    run_until_idle(app)

    job = c.get(f"/api/jobs/{job_id}").json
    assert job["file_counts"] == counts(verified=5, failed=5)
    assert {(p["eqp"], p["reason"]) for p in job["problems"]} == {("6MCDE305", "연결 실패")}
    assert fleet["6MCDE305"].drops == 99 - 3  # 처음 1번 + 재시도 2번
    assert recipe_files(fleet["6MCDE305"]) == {}
    assert recipe_files(fleet["4CCD6701"]) == source_recipe_files(fleet)


def test_an_unreachable_target_fails_with_connection_failure(make, fleet):
    fleet["4CCD6701"].reachable = False
    app, c = make()
    job_id = start(c)
    run_until_idle(app)

    job = c.get(f"/api/jobs/{job_id}").json
    assert job["file_counts"] == counts(verified=5, failed=5)
    assert {(p["eqp"], p["reason"]) for p in job["problems"]} == {("4CCD6701", "연결 실패")}


def test_a_file_over_the_per_transfer_size_cap_is_not_sent(make, fleet):
    app, c = make(transfer_max_bytes=4)
    job_id = start(c, targets=["6MCDE305"])
    run_until_idle(app)

    job = c.get(f"/api/jobs/{job_id}").json
    assert sorted((p["path"], p["reason"]) for p in job["problems"]) == sorted([
        (P1_IDP, "전송 크기 상한 초과"),
        (A_TIF, "전송 크기 상한 초과"),
        (layout.idp_path("ABC_GATE", "W1", "P2"), "전송 크기 상한 초과"),
    ])
    assert set(recipe_files(fleet["6MCDE305"])) == {
        f"{P1_RAW_DIR}/sub/b.csv", f"{layout.raw_dir('ABC_GATE', 'W1', 'P2')}/c.tif"}


class DropOnATifRename(DropOnRenameHandler):
    def ftp_RNTO(self, path):
        if path.endswith("/a.tif"):
            super().ftp_RNTO(path)
        else:
            FTPHandler.ftp_RNTO(self, path)


def test_a_lost_rename_reply_on_a_real_ftp_server_does_not_stall_the_worker(make, fleet, tmp_path):
    root = tmp_path / "ftp"
    os.makedirs(root / layout.ROOT.lstrip("/"))
    server = _Server(root, DropOnATifRename)
    try:
        app, c = make()
        app.config["CONNECT"] = lambda tool: server.client() if tool.eqp_id == "6MCDE305" else fleet[tool.eqp_id]
        job_id = start(c, targets=["6MCDE305"])
        run_until_idle(app)
    finally:
        server.close()

    job = c.get(f"/api/jobs/{job_id}").json
    assert job["file_counts"] == counts(verified=4, uncertain=1)
    [problem] = job["problems"]
    assert (problem["path"], problem["state"], problem["reason"]) == (A_TIF, "uncertain", "rename 응답 유실")
    assert problem["temp_path"].startswith(A_TIF + ".")
    for path, data in source_recipe_files(fleet).items():
        assert (root / path.lstrip("/")).read_bytes() == data


def test_a_refused_login_fails_every_file_of_that_target(make, fleet):
    fleet["6MCDE305"].refuse_login = True
    app, c = make()
    job_id = start(c)
    run_until_idle(app)

    job = c.get(f"/api/jobs/{job_id}").json
    assert job["file_counts"] == counts(verified=5, failed=5)
    assert {(p["eqp"], p["reason"]) for p in job["problems"]} == {("6MCDE305", "로그인 거부: 530 Login incorrect.")}


def test_an_unexpected_worker_error_ends_that_target_without_leaving_files_in_progress(make, fleet):
    def boom(path):
        if path == A_TIF:
            raise ZeroDivisionError("injected")

    fleet["6MCDE305"].on_download = boom  # put 뒤 재다운로드 직전
    app, c = make()
    job_id = start(c)
    run_until_idle(app)

    job = c.get(f"/api/jobs/{job_id}").json
    assert job["file_counts"] == counts(verified=6, uncertain=1, failed=3)
    reason = "작업자 오류: ZeroDivisionError"
    assert sorted((p["eqp"], p["path"], p["state"], p["reason"]) for p in job["problems"]) == sorted([
        ("6MCDE305", A_TIF, "uncertain", reason),
        ("6MCDE305", f"{P1_RAW_DIR}/sub/b.csv", "failed", reason),
        ("6MCDE305", layout.idp_path("ABC_GATE", "W1", "P2"), "failed", reason),
        ("6MCDE305", f"{layout.raw_dir('ABC_GATE', 'W1', 'P2')}/c.tif", "failed", reason),
    ])
    assert recipe_files(fleet["4CCD6701"]) == source_recipe_files(fleet)


def test_an_oversized_snapshot_file_is_refused_without_reading_it_whole(make, fleet, tmp_path):
    # 상한은 manifest 크기로 판정하고 스냅샷을 읽지 않습니다: 내용이 달라도 "스냅샷 손상"이 아닙니다.
    app, c = make(transfer_max_bytes=4)
    job_id = start(c, targets=["6MCDE305"])
    collect(app, store.claim_next_waiting(app.config["DB_PATH"]))
    (tmp_path / "snapshots" / str(job_id) / A_TIF.lstrip("/")).write_bytes(bytes(1024**2))

    transfer_job(app, job_id)

    job = c.get(f"/api/jobs/{job_id}").json
    assert {(p["path"], p["reason"]) for p in job["problems"]} == {
        (P1_IDP, "전송 크기 상한 초과"),
        (A_TIF, "전송 크기 상한 초과"),
        (layout.idp_path("ABC_GATE", "W1", "P2"), "전송 크기 상한 초과"),
    }


def test_a_snapshot_file_that_grew_past_the_cap_after_collection_is_damage_not_size(make, fleet, tmp_path):
    app, c = make(transfer_max_bytes=16)
    job_id = start(c, targets=["6MCDE305"])
    collect(app, store.claim_next_waiting(app.config["DB_PATH"]))
    (tmp_path / "snapshots" / str(job_id) / P1_IDP.lstrip("/")).write_bytes(bytes(17))

    transfer_job(app, job_id)

    job = c.get(f"/api/jobs/{job_id}").json
    assert job["file_counts"] == counts(failed=5)
    assert {p["reason"] for p in job["problems"]} == {"스냅샷 손상"}
    assert recipe_files(fleet["6MCDE305"]) == {}


def test_a_login_refused_on_reconnect_after_put_leaves_that_file_uncertain(make, fleet):
    tool = fleet["6MCDE305"]

    def drop_then_lock_out(path):
        if path == A_TIF:
            tool.refuse_login = True
            raise ConnectionResetError("injected")

    tool.on_download = drop_then_lock_out  # put 뒤 재다운로드 직전
    app, c = make()
    job_id = start(c, targets=["6MCDE305"])
    run_until_idle(app)

    job = c.get(f"/api/jobs/{job_id}").json
    assert job["file_counts"] == counts(verified=1, uncertain=1, failed=3)
    states = {p["path"]: (p["state"], p["reason"]) for p in job["problems"]}
    assert states[A_TIF] == ("uncertain", "검증 전 로그인 거부: 530 Login incorrect.")
    assert {r for st, r in states.values() if st == "failed"} == {"로그인 거부: 530 Login incorrect."}
