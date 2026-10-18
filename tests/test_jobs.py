import hashlib
from datetime import datetime, timedelta

import pytest

import sqlite3

from backend import create_app, layout, store
from backend.fake_fleet import KST, FakeTool
from ftp_handler.core.client import FileInfo
from backend.jobs import run_until_idle
from backend.roster import Tool

ROSTER = [
    Tool("6MCDE301", "192.0.2.11", "M16A", "CG6300", "HITACHI"),
    Tool("6MCDE305", "192.0.2.12", "M16A", "CG6380", "HITACHI"),
    Tool("4CCD6701", "192.0.2.21", "M14", "CG5000", "HITACHI"),
    Tool("6MVRT001", "192.0.2.41", "M16A", "VERITYSEM10", "AMAT"),
]
T = datetime(2026, 10, 18, 9, 30, tzinfo=KST)
P1_RAW = {"a.tif": b"12345", "sub/b.csv": b"xyz"}


@pytest.fixture
def fleet():
    src = FakeTool()
    src.add_recipe("ABC_GATE", "W1", "P1", P1_RAW, T)
    src.add_recipe("ABC_GATE", "W1", "P2", {"c.tif": b"1"}, T)
    src.add(layout.idw_path("ABC_GATE", "W1"), b"IDW", T)
    return {"6MCDE301": src, "6MCDE305": FakeTool(), "4CCD6701": FakeTool()}


@pytest.fixture
def make(tmp_path, fleet):
    def make(**settings):
        app = create_app(
            roster=lambda: ROSTER,
            connect=lambda tool: fleet[tool.eqp_id],
            db_path=tmp_path / "brc.sqlite3",
            snapshot_dir=tmp_path / "snapshots",
            allowed_users={"engineer1", "engineer2"},
            spa_dir=tmp_path / "no-spa",
            scheduler=False,
            **settings,
        )
        c = app.test_client()
        c.set_cookie("LASTUSER", "engineer1")
        return app, c

    return make


@pytest.fixture
def app_client(make):
    return make()


def body(**over):
    return {
        "source_eqp": "6MCDE301",
        "class_name": "ABC_GATE",
        "recipes": [{"idw": "W1", "idp": "P1"}, {"idw": "W1", "idp": "P2"}],
        "targets": ["6MCDE305", "4CCD6701"],
    } | over


def test_created_job_waits_then_collects_a_snapshot_for_every_target(app_client, fleet, tmp_path):
    app, c = app_client
    resp = c.post("/api/jobs", json=body())
    assert resp.status_code == 201
    job_id = resp.json["id"]
    assert resp.json["collection"] == {"state": "waiting", "reason": None}
    assert resp.json["file_counts"]["pending"] == 0

    run_until_idle(app)

    job = c.get(f"/api/jobs/{job_id}").json
    assert job["collection"] == {"state": "done", "reason": None}
    # P1: idp + 2 raw, P2: idp + 1 raw. `.idw` 는 옵션이 꺼져 있어 빠집니다.
    assert job["manifest"]["file_count"] == 5
    # 대상 × 레시피 파일만큼 file 행. 전송 결과는 test_transfer 가 봅니다.
    assert sum(job["file_counts"].values()) == 10
    assert job["executor"] == "engineer1"
    assert job["targets"] == [
        {"eqp": "4CCD6701", "may_create_class": False},
        {"eqp": "6MCDE305", "may_create_class": False},
    ]
    assert job["recipes"] == [{"idw": "W1", "idp": "P1"}, {"idw": "W1", "idp": "P2"}]
    assert job["pairs"] == [
        {"eqp": e, "idw": "W1", "idp": p} for e in ("4CCD6701", "6MCDE305") for p in ("P1", "P2")
    ]
    assert "snapshot_dir" not in job

    src = fleet["6MCDE301"].files
    snap = tmp_path / "snapshots" / str(job_id)
    on_disk = {"/" + str(p.relative_to(snap)): p.read_bytes() for p in snap.rglob("*") if p.is_file()}
    expected = {p: data for p, (data, _) in src.items() if not p.endswith(".idw")}
    assert on_disk == expected
    assert job["manifest"]["total_bytes"] == sum(len(d) for d in expected.values())

    listed = c.get("/api/jobs").json["jobs"]
    assert [(j["id"], j["collection"]["state"], j["target_count"], j["recipe_count"]) for j in listed] == [
        (job_id, "done", 2, 2)
    ]


def test_jobs_are_collected_in_creation_order(app_client):
    app, c = app_client
    first = c.post("/api/jobs", json=body()).json["id"]
    second = c.post("/api/jobs", json=body(recipes=[{"idw": "W1", "idp": "P2"}])).json["id"]
    run_until_idle(app)
    assert [c.get(f"/api/jobs/{i}").json["manifest"]["file_count"] for i in (first, second)] == [5, 2]
    assert [j["id"] for j in c.get("/api/jobs").json["jobs"]] == [second, first]


def test_executor_is_the_lastuser_cookie_not_the_request_body(app_client):
    _, c = app_client
    c.set_cookie("LASTUSER", "engineer2")
    resp = c.post("/api/jobs", json=body(executor="engineer1"))
    assert resp.json["executor"] == "engineer2"


def test_unreachable_source_blocks_collection_with_the_reason(app_client, fleet, tmp_path):
    app, c = app_client
    job_id = c.post("/api/jobs", json=body()).json["id"]
    fleet["6MCDE301"].reachable = False
    run_until_idle(app)
    job = c.get(f"/api/jobs/{job_id}").json
    assert job["collection"]["state"] == "blocked"
    assert "Connection refused" in job["collection"]["reason"]
    assert job["manifest"]["file_count"] == 0
    assert sum(job["file_counts"].values()) == 0
    assert not (tmp_path / "snapshots" / str(job_id)).exists()


def test_missing_raw_folder_blocks_instead_of_copying_a_partial_recipe(app_client, fleet, tmp_path):
    app, c = app_client
    fleet["6MCDE301"].add(layout.idp_path("ABC_GATE", "W1", "P9"), b"IDP", T)
    job_id = c.post("/api/jobs", json=body(recipes=[{"idw": "W1", "idp": "P1"}, {"idw": "W1", "idp": "P9"}])).json["id"]
    run_until_idle(app)
    job = c.get(f"/api/jobs/{job_id}").json
    assert job["collection"]["state"] == "blocked"
    assert "550" in job["collection"]["reason"]
    assert job["manifest"]["file_count"] == 0
    assert not (tmp_path / "snapshots" / str(job_id)).exists()


@pytest.mark.parametrize(
    "over",
    [
        {"class_name": "../etc"},
        {"class_name": ""},
        {"recipes": [{"idw": "W1", "idp": "../P1"}]},
        {"recipes": [{"idw": "..", "idp": "P1"}]},
        {"recipes": [{"idw": "W1", "idp": "a/b"}]},
        {"recipes": [{"idw": "W1", "idp": ""}]},
    ],
)
def test_path_components_that_escape_the_layout_are_refused(app_client, over):
    _, c = app_client
    resp = c.post("/api/jobs", json=body(**over))
    assert resp.status_code == 400
    assert resp.json["error"]["code"] == "bad_path"
    assert c.get("/api/jobs").json == {"jobs": []}


@pytest.mark.parametrize(
    ("over", "code"),
    [
        ({"targets": ["6MCDE305", "6MCDE301"]}, "source_in_targets"),
        ({"targets": ["NOPE"]}, "unknown_tool"),
        ({"targets": ["6MVRT001"]}, "unknown_tool"),
        ({"source_eqp": "6MVRT001"}, "unknown_tool"),
        ({"recipes": []}, "no_recipes"),
        ({"targets": []}, "no_targets"),
        ({"targets": "6MCDE305"}, "bad_request"),
        ({"recipes": [{"idw": "W1", "idp": ["P1"]}]}, "bad_request"),
    ],
)
def test_invalid_selection_is_refused(app_client, over, code):
    _, c = app_client
    resp = c.post("/api/jobs", json=body(**over))
    assert (resp.status_code, resp.json["error"]["code"]) == (400, code)
    assert c.get("/api/jobs").json == {"jobs": []}


def test_more_targets_than_the_thread_cap_is_refused(make):
    _, c = make(max_threads=1)
    resp = c.post("/api/jobs", json=body())
    assert (resp.status_code, resp.json["error"]["code"]) == (400, "too_many_targets")
    assert c.post("/api/jobs", json=body(targets=["6MCDE305"])).status_code == 201


def test_new_job_is_refused_once_snapshots_reach_the_cap(make, fleet):
    cap = len(b"IDP P1\n") + sum(len(d) for d in P1_RAW.values())
    app, c = make(snapshot_cap_bytes=cap)
    assert c.post("/api/jobs", json=body(recipes=[{"idw": "W1", "idp": "P1"}])).status_code == 201
    run_until_idle(app)
    resp = c.post("/api/jobs", json=body())
    assert (resp.status_code, resp.json["error"]["code"]) == (507, "snapshot_full")


def test_unknown_job_is_404(app_client):
    _, c = app_client
    assert c.get("/api/jobs/999").status_code == 404


def test_a_job_left_collecting_by_a_restart_is_not_collected_again(make, fleet):
    # 티켓 12 전까지는 재기동이 미완료 job 을 다시 돌리지 않습니다.
    app, c = make()
    job_id = c.post("/api/jobs", json=body()).json["id"]

    def die(*_, **__):
        raise SystemExit("process killed mid-collection")

    fleet["6MCDE301"].download = die
    with pytest.raises(SystemExit):
        run_until_idle(app)
    del fleet["6MCDE301"].download

    app2, c2 = make()
    run_until_idle(app2)
    job = c2.get(f"/api/jobs/{job_id}").json
    assert job["collection"] == {"state": "collecting", "reason": None}
    assert job["manifest"]["file_count"] == 0


def test_manifest_hash_ignores_order_and_changes_with_content():
    from backend.jobs import manifest_hash

    a = [("/x/a", 1, "aa"), ("/x/b", 2, "bb")]
    assert manifest_hash(a) == manifest_hash(list(reversed(a)))
    assert manifest_hash(a) != manifest_hash([("/x/a", 1, "ab"), ("/x/b", 2, "bb")])
    assert manifest_hash([]) == hashlib.sha256(b"").hexdigest()


def test_file_rows_follow_the_stored_pairs_not_targets_times_recipes(app_client, fleet, tmp_path):
    app, c = app_client
    pairs = [("6MCDE305", "W1", "P1"), ("4CCD6701", "W1", "P2")]
    job_id = store.create_job(
        app.config["DB_PATH"], executor="engineer1", source_eqp="6MCDE301", class_name="ABC_GATE", pairs=pairs
    )
    run_until_idle(app)
    job = c.get(f"/api/jobs/{job_id}").json
    assert job["collection"] == {"state": "done", "reason": None}
    assert job["pairs"] == [
        {"eqp": "4CCD6701", "idw": "W1", "idp": "P2"},
        {"eqp": "6MCDE305", "idw": "W1", "idp": "P1"},
    ]
    # 스냅샷은 두 레시피의 합집합(P1 3개 + P2 2개), file 행은 쌍마다 그 레시피의 파일만.
    assert job["manifest"]["file_count"] == 5
    assert sum(job["file_counts"].values()) == 3 + 2


class EscapingTool(FakeTool):
    """raw 폴더 LIST 에 `../` 로 스냅샷 밖을 가리키는 항목을 끼워 넣는 장비."""

    EVIL = "../" * 9 + "escape.txt"

    def list_details(self, remote_dir, pattern=None, **kw):
        out = super().list_details(remote_dir, pattern, **kw)
        if remote_dir == layout.raw_dir("ABC_GATE", "W1", "P1"):
            path = f"{remote_dir}/{self.EVIL}"
            self.files[path] = (b"pwn", T)
            out.append(FileInfo(self.EVIL, path, False, 3, T, ""))
        return out


def test_listing_entry_that_escapes_its_folder_blocks_and_writes_nothing_outside(make, fleet, tmp_path):
    evil = EscapingTool()
    evil.files = fleet["6MCDE301"].files
    fleet["6MCDE301"] = evil
    app, c = make()
    job_id = c.post("/api/jobs", json=body(recipes=[{"idw": "W1", "idp": "P1"}])).json["id"]
    run_until_idle(app)
    job = c.get(f"/api/jobs/{job_id}").json
    assert job["collection"]["state"] == "blocked"
    assert job["collection"]["reason"] == (
        f"수집 오류: ValueError: 경로 성분으로 쓸 수 없는 값입니다: {EscapingTool.EVIL!r}"
    )
    assert sum(job["file_counts"].values()) == 0
    assert not (tmp_path / "escape.txt").exists()
    assert not (tmp_path / "snapshots" / str(job_id)).exists()


def test_collection_stops_at_the_snapshot_cap_even_for_jobs_created_while_disk_was_empty(make, tmp_path):
    p1 = len(b"IDP P1\n") + sum(len(d) for d in P1_RAW.values())
    app, c = make(snapshot_cap_bytes=p1 + 1)
    one = [{"idw": "W1", "idp": "P1"}]
    first = c.post("/api/jobs", json=body(recipes=one)).json["id"]
    second = c.post("/api/jobs", json=body(recipes=one)).json["id"]
    run_until_idle(app)
    states = [c.get(f"/api/jobs/{i}").json["collection"] for i in (first, second)]
    assert states[0] == {"state": "done", "reason": None}
    assert states[1] == {"state": "blocked", "reason": f"수집 오류: 스냅샷 총량 상한 {p1 + 1} B를 넘습니다."}
    from backend.jobs import snapshot_bytes

    assert snapshot_bytes(tmp_path / "snapshots") == p1
    assert not (tmp_path / "snapshots" / str(second)).exists()


def test_a_failed_completion_write_blocks_the_job_instead_of_leaving_it_collecting(app_client, monkeypatch, tmp_path):
    # SQLite 잠금은 API 로 만들 수 없어 완료 기록 함수만 바꿉니다.
    app, c = app_client
    job_id = c.post("/api/jobs", json=body()).json["id"]

    def locked(*_, **__):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(store, "finish_collection", locked)
    run_until_idle(app)
    job = c.get(f"/api/jobs/{job_id}").json
    assert job["collection"] == {"state": "blocked", "reason": "수집 오류: OperationalError: database is locked"}
    assert not (tmp_path / "snapshots" / str(job_id)).exists()


def test_local_write_errors_do_not_leak_local_paths(app_client, tmp_path):
    app, c = app_client
    job_id = c.post("/api/jobs", json=body()).json["id"]
    # job 스냅샷 폴더 자리에 파일을 두어 mkdir 이 실패하게 합니다.
    (tmp_path / "snapshots" / str(job_id)).write_bytes(b"")
    run_until_idle(app)
    job = c.get(f"/api/jobs/{job_id}").json
    assert job["collection"]["state"] == "blocked"
    assert job["collection"]["reason"].startswith("수집 오류: 로컬 저장 실패(")
    assert str(tmp_path) not in job["collection"]["reason"]


def test_a_symlink_loop_in_the_snapshot_folder_does_not_leak_local_paths(app_client, tmp_path):
    app, c = app_client
    job_id = c.post("/api/jobs", json=body()).json["id"]
    snap = tmp_path / "snapshots" / str(job_id)
    snap.symlink_to(snap)
    run_until_idle(app)
    job = c.get(f"/api/jobs/{job_id}").json
    assert job["collection"]["state"] == "blocked"
    assert job["collection"]["reason"].startswith("수집 오류: 로컬 저장 실패(")
    assert str(tmp_path) not in job["collection"]["reason"]


def _edit_a_tif_during(src, passes):
    """수집 pass 의 마지막 download 때(이미 받은 뒤) 원본 `a.tif`를 고칩니다. pass 는 5파일."""
    a_tif = layout.raw_dir("ABC_GATE", "W1", "P1") + "/a.tif"
    calls = []

    def edit(path):
        calls.append(path)
        n, nth = divmod(len(calls) - 1, 5)
        if nth == 4 and n < passes:
            src.add(a_tif, f"edited {n}".encode(), T + timedelta(minutes=n + 1))

    src.on_download = edit
    return a_tif


def test_source_edited_during_the_first_pass_is_collected_again_with_the_new_bytes(app_client, fleet, tmp_path):
    app, c = app_client
    a_tif = _edit_a_tif_during(fleet["6MCDE301"], passes=1)
    job_id = c.post("/api/jobs", json=body()).json["id"]
    run_until_idle(app)
    job = c.get(f"/api/jobs/{job_id}").json
    assert job["collection"] == {"state": "done", "reason": None}
    assert job["manifest"]["file_count"] == 5
    assert (tmp_path / "snapshots" / str(job_id) / a_tif.lstrip("/")).read_bytes() == b"edited 0"


def test_source_edited_during_both_passes_blocks_and_leaves_no_snapshot(app_client, fleet, tmp_path):
    app, c = app_client
    _edit_a_tif_during(fleet["6MCDE301"], passes=2)
    job_id = c.post("/api/jobs", json=body()).json["id"]
    run_until_idle(app)
    job = c.get(f"/api/jobs/{job_id}").json
    assert job["collection"] == {"state": "blocked", "reason": "원본이 바뀌는 중"}
    assert job["manifest"]["file_count"] == 0
    assert sum(job["file_counts"].values()) == 0
    assert not (tmp_path / "snapshots" / str(job_id)).exists()


def test_a_listing_that_only_changes_order_is_not_a_source_change(app_client, fleet):
    app, c = app_client
    src = fleet["6MCDE301"]
    plain = src.list_details
    flips = []

    def flipping(remote_dir, *args, **kwargs):
        flips.append(remote_dir)
        out = plain(remote_dir, *args, **kwargs)
        return out[::-1] if len(flips) % 2 else out

    src.list_details = flipping
    job_id = c.post("/api/jobs", json=body()).json["id"]
    run_until_idle(app)
    assert c.get(f"/api/jobs/{job_id}").json["collection"] == {"state": "done", "reason": None}


def test_an_idp_briefly_missing_after_the_first_pass_is_collected_again(app_client, fleet):
    # 편집기가 .idp를 지웠다 다시 쓰는 사이에 수집 후 목록이 걸린 경우입니다.
    app, c = app_client
    src = fleet["6MCDE301"]
    idp = layout.idp_path("ABC_GATE", "W1", "P1")
    saved = src.files[idp]
    downloads = []

    def remove_on_last_download_of_first_pass(path):
        downloads.append(path)
        if len(downloads) == 5:
            del src.files[idp]

    plain = src.list_details

    def restore_after_it_was_seen_missing(remote_dir, *args, **kwargs):
        out = plain(remote_dir, *args, **kwargs)
        if idp not in src.files and remote_dir == layout.idw_dir("ABC_GATE", "W1"):
            src.files[idp] = saved
        return out

    src.on_download = remove_on_last_download_of_first_pass
    src.list_details = restore_after_it_was_seen_missing
    job_id = c.post("/api/jobs", json=body()).json["id"]
    run_until_idle(app)
    job = c.get(f"/api/jobs/{job_id}").json
    assert job["collection"] == {"state": "done", "reason": None}
    assert job["manifest"]["file_count"] == 5


def test_snapshot_total_skips_files_and_folders_deleted_while_counting(tmp_path, monkeypatch):
    import os

    from backend import jobs

    (tmp_path / "1" / "gone").mkdir(parents=True)
    (tmp_path / "1" / "kept").write_bytes(b"1234")
    (tmp_path / "1" / "deleted").write_bytes(b"12345678")
    (tmp_path / "1" / "gone" / "x").write_bytes(b"12345678")
    walk = os.walk

    # 목록을 읽은 뒤 stat 전에 닫기가 파일과 폴더를 지웁니다.
    def walk_then_delete(*args, **kwargs):
        for d, dirs, names in walk(*args, **kwargs):
            if d.endswith("1"):
                os.remove(os.path.join(d, "deleted"))
                jobs.shutil.rmtree(os.path.join(d, "gone"))
            yield d, dirs, names

    monkeypatch.setattr(jobs.os, "walk", walk_then_delete)
    assert jobs.snapshot_bytes(tmp_path) == 4
