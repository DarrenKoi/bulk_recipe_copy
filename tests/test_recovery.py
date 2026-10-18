"""재기동 대조 복구. 같은 SQLite·같은 가짜 장비 위에 앱을 다시 만들어 시험합니다(스펙 "시험")."""

import shutil
import sqlite3
from ftplib import error_perm

import pytest
from test_transfer import (  # noqa: F401  (fleet, make 는 pytest fixture)
    A_TIF,
    P1_IDP,
    P1_RAW_DIR,
    T,
    counts,
    fleet,
    make,
    recipe_files,
    source_recipe_files,
    start,
)

from backend import store
from backend.jobs import run_until_idle

B_CSV = f"{P1_RAW_DIR}/sub/b.csv"


def crash_before_verifying(tool, path):
    """`path` 의 put 이 끝나고 재다운로드(검증) 직전에 프로세스가 죽습니다."""

    def die(p):
        if p == path:
            tool.on_download = None
            raise SystemExit("process killed")

    tool.on_download = die


def crash(app):
    with pytest.raises(SystemExit):
        run_until_idle(app)


def recover(make, **settings):
    """새 앱의 첫 시작 판정(= 재기동 대조 복구)까지만. (앱, 클라이언트, 이어 돌릴 job)."""
    app, c = make(**settings)
    return app, c, app.config["SCHEDULER"].admit()


def finish(app, claimed):
    for job in claimed:
        app.config["SCHEDULER"].run(job, parallel=False)
    run_until_idle(app)


def states(c, job_id) -> dict[str, tuple[str, str | None]]:
    return {f["path"]: (f["state"], f["reason"]) for r in c.get(f"/api/jobs/{job_id}").json["tree"][0]["recipes"]
            for f in r["files"]}


def test_a_file_uploaded_before_the_restart_is_verified_without_rewriting_and_the_rest_is_sent(make, fleet):
    tool = fleet["6MCDE305"]
    app, c = make()
    job_id = start(c, targets=["6MCDE305"])
    crash_before_verifying(tool, A_TIF)
    crash(app)
    assert c.get(f"/api/jobs/{job_id}").json["file_counts"] == counts(verified=1, in_progress=1, pending=3)
    written_at = tool.files[A_TIF][1]
    tool.add(A_TIF + ".9f8e7d.part", b"stray", T)  # 출처 모를 임시 파일

    app, c, claimed = recover(make)
    assert c.get(f"/api/jobs/{job_id}").json["file_counts"] == counts(verified=2, pending=3)
    finish(app, claimed)

    job = c.get(f"/api/jobs/{job_id}").json
    assert (job["file_counts"], job["progress"]["success"]) == (counts(verified=5), True)
    assert tool.files[A_TIF][1] == written_at
    assert recipe_files(tool) == source_recipe_files(fleet) | {A_TIF + ".9f8e7d.part": b"stray"}


def test_a_file_whose_put_never_landed_goes_back_to_pending_and_is_sent(make, fleet):
    tool, put = fleet["6MCDE305"], fleet["6MCDE305"].put

    def die_on_a_tif(path, data, **kw):
        if path == A_TIF:
            raise SystemExit("process killed")
        return put(path, data, **kw)

    tool.put = die_on_a_tif
    app, c = make()
    job_id = start(c, targets=["6MCDE305"])
    crash(app)
    del tool.put

    app, c, claimed = recover(make)
    assert states(c, job_id)[A_TIF] == ("pending", None)
    finish(app, claimed)
    assert c.get(f"/api/jobs/{job_id}").json["file_counts"] == counts(verified=5)
    assert recipe_files(tool) == source_recipe_files(fleet)


def test_a_target_file_that_differs_goes_back_to_pending_and_is_not_overwritten_without_permission(make, fleet):
    tool = fleet["6MCDE305"]
    app, c = make()
    job_id = start(c, targets=["6MCDE305"])
    crash_before_verifying(tool, A_TIF)
    crash(app)
    tool.add(A_TIF, b"other", T)  # 재기동 사이에 누가 바꿈

    app, c, claimed = recover(make)
    assert states(c, job_id)[A_TIF] == ("pending", None)
    finish(app, claimed)
    assert states(c, job_id)[A_TIF] == ("failed", "확인 뒤 대상에 생김")
    assert c.get(f"/api/jobs/{job_id}").json["file_counts"] == counts(verified=4, failed=1)
    assert tool.files[A_TIF][0] == b"other"


def test_an_unreachable_target_leaves_the_file_uncertain(make, fleet):
    tool = fleet["6MCDE305"]
    app, c = make()
    job_id = start(c, targets=["6MCDE305"])
    crash_before_verifying(tool, A_TIF)
    crash(app)
    tool.reachable = False

    app, c = make()
    run_until_idle(app)
    assert states(c, job_id)[A_TIF] == ("uncertain", "재기동 때 전송 중")
    assert c.get(f"/api/jobs/{job_id}").json["file_counts"] == counts(verified=1, uncertain=1, failed=3)


def test_a_550_on_a_file_the_folder_still_lists_is_not_absence(make, fleet):
    tool = fleet["6MCDE305"]
    app, c = make()
    job_id = start(c, targets=["6MCDE305"])
    crash_before_verifying(tool, A_TIF)
    crash(app)
    download = tool.download

    def refuse_a_tif(path, **kw):
        if path == A_TIF:
            raise error_perm("550 Permission denied.")
        return download(path, **kw)

    tool.download = refuse_a_tif

    app, c, _ = recover(make)
    assert states(c, job_id)[A_TIF] == ("uncertain", "재기동 때 전송 중")


def test_an_uncertain_file_from_a_finished_job_is_reconciled_too(make, fleet, tmp_path):
    fleet["6MCDE305"].lose_rename_reply.add(A_TIF)  # rename 은 일어났고 응답만 사라짐
    app, c = make()
    job_id = start(c, targets=["6MCDE305"])
    run_until_idle(app)
    assert states(c, job_id)[A_TIF][0] == "uncertain"

    app, c = make()
    run_until_idle(app)
    job = c.get(f"/api/jobs/{job_id}").json
    assert (job["file_counts"], job["progress"]["success"]) == (counts(verified=5), True)
    assert not (tmp_path / "snapshots" / str(job_id)).exists()


def test_a_job_left_collecting_is_collected_again_and_sent(make, fleet):
    app, c = make()
    job_id = start(c)

    def die(*_, **__):
        raise SystemExit("process killed mid-collection")

    fleet["6MCDE301"].download = die
    crash(app)
    del fleet["6MCDE301"].download
    assert c.get(f"/api/jobs/{job_id}").json["collection"] == {"state": "collecting", "reason": None}

    app, c = make()
    run_until_idle(app)
    job = c.get(f"/api/jobs/{job_id}").json
    assert (job["collection"]["state"], job["file_counts"]) == ("done", counts(verified=10))
    assert recipe_files(fleet["6MCDE305"]) == recipe_files(fleet["4CCD6701"]) == source_recipe_files(fleet)


def test_a_job_left_collecting_with_a_cancel_is_blocked_not_collected(make, fleet):
    app, c = make()
    job_id = start(c)
    fleet["6MCDE301"].download = lambda *_, **__: (_ for _ in ()).throw(SystemExit("killed"))
    crash(app)
    del fleet["6MCDE301"].download
    assert c.post(f"/api/jobs/{job_id}/cancel").status_code == 200

    app, c = make()
    run_until_idle(app)
    job = c.get(f"/api/jobs/{job_id}").json
    assert (job["collection"], job["file_counts"]) == ({"state": "blocked", "reason": "취소"}, counts())
    assert recipe_files(fleet["6MCDE305"]) == {}


def test_the_cancel_flag_is_applied_again_after_reconciliation(make, fleet):
    tool = fleet["6MCDE305"]
    app, c = make()
    job_id = start(c, targets=["6MCDE305"])
    crash_before_verifying(tool, A_TIF)
    crash(app)
    assert c.post(f"/api/jobs/{job_id}/cancel").status_code == 200

    app, c = make()
    run_until_idle(app)
    assert c.get(f"/api/jobs/{job_id}").json["file_counts"] == counts(verified=2, failed=3)
    assert {r for st, r in states(c, job_id).values() if st == "failed"} == {"취소"}
    assert set(recipe_files(tool)) == {P1_IDP, A_TIF}


def test_a_started_job_over_a_lowered_cap_fails_its_pending_files(make, fleet):
    app, c = make()
    job_id = start(c)  # 4CCD6701 작업자가 먼저 돕니다
    crash_before_verifying(fleet["4CCD6701"], P1_IDP)
    crash(app)

    app, c = make(max_threads=1)  # 재기동하며 상한을 줄임
    run_until_idle(app)
    job = c.get(f"/api/jobs/{job_id}").json
    assert job["file_counts"] == counts(verified=1, failed=9)
    assert {p["reason"] for p in job["problems"]} == {"상한 초과: 대상 2대 > 스레드 상한 1"}


def test_a_snapshot_lost_in_the_restart_is_re_collected_and_adopted(make, fleet, tmp_path):
    tool = fleet["6MCDE305"]
    app, c = make()
    job_id = start(c, targets=["6MCDE305"])
    crash_before_verifying(tool, A_TIF)
    crash(app)
    shutil.rmtree(tmp_path / "snapshots")

    app, c = make()
    run_until_idle(app)
    job = c.get(f"/api/jobs/{job_id}").json
    assert (job["file_counts"], job["untransferable"]) == (counts(verified=5), None)
    assert recipe_files(tool) == source_recipe_files(fleet)
    assert list((tmp_path / "snapshots").iterdir()) == []


def test_an_untransferable_job_only_reconciles_after_the_restart(make, fleet, tmp_path):
    tool, src = fleet["6MCDE305"], fleet["6MCDE301"]
    app, c = make()
    job_id = start(c, targets=["6MCDE305"])

    def damage_and_change_source(path):
        if path == P1_IDP:  # 첫 파일 검증 중: 뒤 파일의 스냅샷이 상하고 원본도 바뀜
            (tmp_path / "snapshots" / str(job_id) / B_CSV.lstrip("/")).write_bytes(b"XYZ")
            src.add(B_CSV, b"xyz2", T)

    tool.on_download = damage_and_change_source
    tool.lose_rename_reply.add(A_TIF)
    run_until_idle(app)
    job = c.get(f"/api/jobs/{job_id}").json
    assert job["untransferable"] == "원본이 job 확인 때와 다름"
    assert states(c, job_id)[A_TIF] == ("uncertain", "rename 응답 유실")
    assert job["file_counts"] == counts(verified=1, uncertain=1, failed=3)
    assert B_CSV not in tool.files

    tool.on_download, tool.lose_rename_reply = None, set()
    del tool.files[A_TIF]  # 재기동 사이에 대상에서 사라짐
    app, c = make()
    run_until_idle(app)
    job = c.get(f"/api/jobs/{job_id}").json
    assert states(c, job_id)[A_TIF] == ("failed", "job 확인 뒤 원본이 바뀜")
    assert (job["file_counts"], job["untransferable"]) == (counts(verified=1, failed=4), "원본이 job 확인 때와 다름")
    assert set(recipe_files(tool)) == {P1_IDP}


def test_a_database_from_before_this_column_existed_is_upgraded(make, tmp_path):
    db = tmp_path / "brc.sqlite3"
    make()
    with sqlite3.connect(db) as conn:
        conn.execute("ALTER TABLE jobs DROP COLUMN untransferable")
    app, c = make()
    job_id = start(c, targets=["6MCDE305"])
    assert c.get(f"/api/jobs/{job_id}").json["untransferable"] is None
    assert store.get_job(db, job_id)["untransferable"] is None
