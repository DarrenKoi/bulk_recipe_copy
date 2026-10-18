import pytest
from test_transfer import ROSTER, counts, fleet, recipe_files  # noqa: F401  (fleet 는 fixture)

from backend import create_app, layout, store
from backend.jobs import collect, run_until_idle


@pytest.fixture
def app(tmp_path, fleet):  # noqa: F811
    return create_app(
        roster=lambda: ROSTER,
        connect=lambda tool: fleet[tool.eqp_id],
        db_path=tmp_path / "brc.sqlite3",
        snapshot_dir=tmp_path / "snapshots",
        allowed_users={"engineer1", "engineer2"},
        spa_dir=tmp_path / "no-spa",
        scheduler=False,
    )


def client(app, user="engineer1"):
    c = app.test_client()
    c.set_cookie("LASTUSER", user)
    return c


@pytest.fixture
def c(app):
    return client(app)


def start(c) -> int:
    resp = c.post("/api/jobs", json={
        "source_eqp": "6MCDE301",
        "class_name": "ABC_GATE",
        "recipes": [{"idw": "W1", "idp": "P1"}, {"idw": "W1", "idp": "P2"}],
        "targets": ["6MCDE305", "4CCD6701"],
        "create_class": ["6MCDE305", "4CCD6701"],
    })
    assert resp.status_code == 201
    return resp.json["id"]


def snapshot(tmp_path, job_id):
    return tmp_path / "snapshots" / str(job_id)


def test_a_waiting_job_cancelled_before_admission_is_blocked_without_collecting(app, c, fleet, tmp_path):
    job_id = start(c)
    resp = c.post(f"/api/jobs/{job_id}/cancel")
    assert resp.status_code == 200
    assert resp.json["collection"] == {"state": "blocked", "reason": "취소"}

    run_until_idle(app)
    job = c.get(f"/api/jobs/{job_id}").json
    assert job["collection"] == {"state": "blocked", "reason": "취소"}
    assert job["file_counts"] == counts()
    assert not snapshot(tmp_path, job_id).exists()
    assert recipe_files(fleet["6MCDE305"]) == {}


def test_a_job_cancelled_after_admission_but_before_collecting_is_blocked(app, c, fleet, tmp_path):
    job_id = start(c)
    job = store.claim_next_waiting(app.config["DB_PATH"])
    assert c.post(f"/api/jobs/{job_id}/cancel").status_code == 200
    collect(app, job)

    got = c.get(f"/api/jobs/{job_id}").json
    assert got["collection"] == {"state": "blocked", "reason": "취소"}
    assert not snapshot(tmp_path, job_id).exists()


def test_a_job_cancelled_while_collecting_stops_between_downloads_and_drops_the_snapshot(app, c, fleet, tmp_path):
    job_id = start(c)
    downloads = []

    def cancel_on_second(path):
        downloads.append(path)
        if len(downloads) == 2:
            c.post(f"/api/jobs/{job_id}/cancel")

    fleet["6MCDE301"].on_download = cancel_on_second
    run_until_idle(app)

    job = c.get(f"/api/jobs/{job_id}").json
    assert job["collection"] == {"state": "blocked", "reason": "취소"}
    assert len(downloads) == 2
    assert not snapshot(tmp_path, job_id).exists()
    assert recipe_files(fleet["6MCDE305"]) == {}


def test_a_job_cancelled_mid_transfer_finishes_the_file_in_flight_and_fails_the_rest(app, c, fleet, tmp_path):
    job_id = start(c)

    # 작업자는 대상 eqp 순으로 돕니다(순차 모드). 첫 대상의 첫 재다운로드(검증) 때 취소. 그 파일은 put 을 마쳤고 검증까지 갑니다.
    def cancel_once(path):
        fleet["4CCD6701"].on_download = None
        c.post(f"/api/jobs/{job_id}/cancel")

    fleet["4CCD6701"].on_download = cancel_once
    run_until_idle(app)

    job = c.get(f"/api/jobs/{job_id}").json
    by_eqp = {t["eqp"]: t for t in job["tree"]}
    assert by_eqp["4CCD6701"]["counts"] == counts(verified=1, failed=4)
    assert by_eqp["6MCDE305"]["counts"] == counts(failed=5)
    assert {p["reason"] for p in job["problems"]} == {"취소"}
    assert len(recipe_files(fleet["4CCD6701"])) == 1
    assert recipe_files(fleet["6MCDE305"]) == {}
    assert job["progress"] == {
        "status": "failed", "finished": True, "success": False, "processed": 10, "total": 10, "rate": 1.0,
    }
    assert snapshot(tmp_path, job_id).exists()


def test_a_cancel_during_a_reconnect_still_verifies_the_claimed_file(app, c, fleet):
    job_id = start(c)

    # 첫 대상의 첫 파일 put 직후(검증 전) 취소하고 연결을 끊습니다. 재연결한 작업자는 잡아 둔 그 파일을 검증까지 마칩니다.
    def cancel_and_drop(path):
        fleet["4CCD6701"].on_download = None
        c.post(f"/api/jobs/{job_id}/cancel")
        raise ConnectionResetError("injected")

    fleet["4CCD6701"].on_download = cancel_and_drop
    run_until_idle(app)

    job = c.get(f"/api/jobs/{job_id}").json
    by_eqp = {t["eqp"]: t for t in job["tree"]}
    assert by_eqp["4CCD6701"]["counts"] == counts(verified=1, failed=4)
    assert by_eqp["6MCDE305"]["counts"] == counts(failed=5)
    assert {p["reason"] for p in job["problems"]} == {"취소"}
    assert job["progress"]["finished"] is True


def test_a_cancel_racing_the_last_file_is_refused_and_leaves_no_flag(app, c, monkeypatch):
    job_id = start(c)
    request_cancel = store.request_cancel

    def finish_first(db, jid, requested=True):
        if requested:  # 플래그를 세우기 직전에 job 이 끝납니다.
            run_until_idle(app)
        request_cancel(db, jid, requested)

    monkeypatch.setattr(store, "request_cancel", finish_first)
    resp = c.post(f"/api/jobs/{job_id}/cancel")
    assert (resp.status_code, resp.json["error"]["code"]) == (409, "finished")
    job = c.get(f"/api/jobs/{job_id}").json
    assert (job["progress"]["success"], job["cancel_requested"]) == (True, False)


def test_only_the_executor_may_cancel_or_close(app, c):
    job_id = start(c)
    other = client(app, "engineer2")
    assert other.post(f"/api/jobs/{job_id}/cancel").status_code == 403
    assert c.get(f"/api/jobs/{job_id}").json["cancel_requested"] is False

    c.post(f"/api/jobs/{job_id}/cancel")
    assert other.post(f"/api/jobs/{job_id}/close").status_code == 403
    assert c.get(f"/api/jobs/{job_id}").json["closed"] is False


def test_a_running_job_cannot_be_closed_and_a_finished_one_cannot_be_cancelled(app, c):
    job_id = start(c)
    resp = c.post(f"/api/jobs/{job_id}/close")
    assert (resp.status_code, resp.json["error"]["code"]) == (409, "not_finished")

    run_until_idle(app)
    resp = c.post(f"/api/jobs/{job_id}/cancel")
    assert (resp.status_code, resp.json["error"]["code"]) == (409, "finished")


def test_unknown_job_cancel_and_close_are_404(c):
    assert c.post("/api/jobs/99/cancel").status_code == 404
    assert c.post("/api/jobs/99/close").status_code == 404


def test_a_fully_verified_job_drops_its_snapshot_immediately(app, c, tmp_path):
    job_id = start(c)
    run_until_idle(app)
    assert c.get(f"/api/jobs/{job_id}").json["progress"]["success"] is True
    assert not snapshot(tmp_path, job_id).exists()


def test_a_failed_job_keeps_its_snapshot_until_closed_then_leaves_the_list(app, c, fleet, tmp_path):
    fleet["6MCDE305"].corrupt_downloads.add(layout.raw_dir("ABC_GATE", "W1", "P1") + "/a.tif")
    job_id = start(c)
    run_until_idle(app)
    assert snapshot(tmp_path, job_id).exists()

    resp = c.post(f"/api/jobs/{job_id}/close")
    assert (resp.status_code, resp.json["closed"]) == (200, True)
    assert not snapshot(tmp_path, job_id).exists()
    assert c.get("/api/jobs").json["jobs"] == []
    assert c.get(f"/api/jobs/{job_id}").status_code == 200
