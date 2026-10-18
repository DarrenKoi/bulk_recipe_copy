from ftplib import error_perm

from test_transfer import A_TIF, P1_IDP, P1_RAW_DIR, T, counts, fleet, make, start  # noqa: F401

from backend import layout, store, transfer
from backend.jobs import collect, run_until_idle

B_CSV = f"{P1_RAW_DIR}/sub/b.csv"
P2_RAW_DIR = layout.raw_dir("ABC_GATE", "W1", "P2")
P2_IDP = layout.idp_path("ABC_GATE", "W1", "P2")


def target(job, eqp):
    [t] = [t for t in job["tree"] if t["eqp"] == eqp]
    return t


def recipe(job, eqp, idp):
    [r] = [r for r in target(job, eqp)["recipes"] if r["idp"] == idp]
    return r


def verified(*paths):
    return [{"path": p, "state": "verified", "reason": None, "temp_path": None} for p in paths]


def test_a_fully_verified_job_is_a_success_with_every_target_verified_and_no_target_only_files(make):
    app, c = make()
    job_id = start(c)
    run_until_idle(app)

    job = c.get(f"/api/jobs/{job_id}").json
    assert job["progress"] == {
        "status": "done", "finished": True, "success": True, "processed": 10, "total": 10, "rate": 1.0}
    assert [(t["eqp"], t["state"]) for t in job["tree"]] == [("4CCD6701", "verified"), ("6MCDE305", "verified")]
    assert target(job, "6MCDE305")["recipes"] == [
        {"idw": "W1", "idp": "P1", "target_only": 0, "files": verified(P1_IDP, A_TIF, B_CSV)},
        {"idw": "W1", "idp": "P2", "target_only": 0, "files": verified(P2_IDP, f"{P2_RAW_DIR}/c.tif")},
    ]
    assert "snapshot_dir" not in job
    assert str(app.config["SNAPSHOT_DIR"]) not in str(job)


def test_all_files_processed_with_a_failure_is_100_percent_but_not_a_success(make, fleet):
    fleet["6MCDE305"].corrupt_downloads.add(A_TIF)
    app, c = make()
    job_id = start(c)
    run_until_idle(app)

    job = c.get(f"/api/jobs/{job_id}").json
    assert job["progress"] == {
        "status": "failed", "finished": True, "success": False, "processed": 10, "total": 10, "rate": 1.0}
    assert target(job, "6MCDE305")["state"] == "failed"
    assert target(job, "4CCD6701")["state"] == "verified"
    assert recipe(job, "6MCDE305", "P1")["files"][1] == {
        "path": A_TIF, "state": "failed", "reason": "해시 불일치", "temp_path": None}
    assert c.get("/api/jobs").json["jobs"][0]["progress"]["status"] == "failed"


def test_an_uncertain_file_outranks_a_failed_one_on_the_same_target(make, fleet):
    fleet["6MCDE305"].lose_rename_reply.add(A_TIF)
    fleet["6MCDE305"].corrupt_downloads.add(B_CSV)
    app, c = make()
    job_id = start(c)
    run_until_idle(app)

    t = target(c.get(f"/api/jobs/{job_id}").json, "6MCDE305")
    assert t["state"] == "uncertain"
    assert t["counts"] == counts(verified=3, failed=1, uncertain=1)


def test_before_collection_there_is_no_rate_and_no_success(make):
    app, c = make()
    job_id = start(c)

    job = c.get(f"/api/jobs/{job_id}").json
    assert job["progress"] == {
        "status": "waiting", "finished": False, "success": False, "processed": 0, "total": 0, "rate": None}
    assert target(job, "6MCDE305") == {
        "eqp": "6MCDE305",
        "state": None,
        "counts": counts(),
        "recipes": [
            {"idw": "W1", "idp": "P1", "target_only": None, "files": []},
            {"idw": "W1", "idp": "P2", "target_only": None, "files": []},
        ],
    }


def test_a_blocked_collection_is_a_finished_failure_without_a_rate(make, fleet):
    fleet["6MCDE301"].reachable = False
    app, c = make()
    job_id = start(c)
    run_until_idle(app)

    job = c.get(f"/api/jobs/{job_id}").json
    assert job["progress"] == {
        "status": "failed", "finished": True, "success": False, "processed": 0, "total": 0, "rate": None}
    assert [t["state"] for t in job["tree"]] == [None, None]


def test_a_target_with_files_left_is_running_and_shows_only_its_problem_counts(make):
    app, c = make()
    job_id = start(c)
    db = app.config["DB_PATH"]
    collect(app, store.claim_next_waiting(db))
    store.move_file(db, job_id, "6MCDE305", A_TIF, "pending", "failed", reason="장비 거부: 550")
    store.move_file(db, job_id, "6MCDE305", B_CSV, "pending", "in_progress")

    job = c.get(f"/api/jobs/{job_id}").json
    assert job["progress"] == {
        "status": "running", "finished": False, "success": False, "processed": 1, "total": 10, "rate": 0.1}
    t = target(job, "6MCDE305")
    assert t["state"] == "running"
    assert (t["counts"]["failed"], t["counts"]["uncertain"]) == (1, 0)
    assert target(job, "4CCD6701")["state"] == "running"
    assert c.get("/api/jobs").json["jobs"][0]["progress"]["status"] == "running"


def test_files_only_on_the_target_raw_folder_are_counted_per_recipe(make, fleet):
    tool = fleet["6MCDE305"]

    def someone_else_writes(path):
        if path == A_TIF:  # 첫 쓰기 전 재확인 뒤에 다른 사람이 옛 파일을 남긴 것처럼
            tool.add(f"{P1_RAW_DIR}/old.tif", b"old", T)
            tool.add(f"{P1_RAW_DIR}/sub/old.csv", b"old", T)

    tool.on_download = someone_else_writes
    app, c = make()
    job_id = start(c)
    run_until_idle(app)

    job = c.get(f"/api/jobs/{job_id}").json
    assert target(job, "6MCDE305")["state"] == "verified"
    assert [r["target_only"] for r in target(job, "6MCDE305")["recipes"]] == [2, 0]
    assert [r["target_only"] for r in target(job, "4CCD6701")["recipes"]] == [0, 0]


def test_a_refused_raw_listing_leaves_the_target_only_count_unknown_not_zero(make, fleet):
    tool = fleet["6MCDE305"]
    real = tool.list_details

    def refuse_raw(remote_dir, *args, **kwargs):
        if remote_dir == P1_RAW_DIR:
            raise error_perm(f"550 {remote_dir}: Permission denied")
        return real(remote_dir, *args, **kwargs)

    tool.list_details = refuse_raw
    app, c = make()
    job_id = start(c)
    run_until_idle(app)

    job = c.get(f"/api/jobs/{job_id}").json
    assert target(job, "6MCDE305")["state"] == "verified"
    assert [r["target_only"] for r in target(job, "6MCDE305")["recipes"]] == [None, 0]


def test_a_raw_listing_over_the_scan_cap_is_unknown_and_the_copy_goes_on(make, monkeypatch):
    monkeypatch.setattr(transfer, "_TARGET_ONLY_SCAN_CAP", 1)  # P1 raw 에는 a.tif 와 sub/ 두 항목
    app, c = make()
    job_id = start(c)
    run_until_idle(app)

    job = c.get(f"/api/jobs/{job_id}").json
    assert job["file_counts"] == counts(verified=10)
    assert [r["target_only"] for r in target(job, "6MCDE305")["recipes"]] == [None, 0]
