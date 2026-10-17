import hashlib
from datetime import datetime

import pytest

from backend import create_app, layout
from backend.fake_fleet import KST, FakeTool
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
    assert job["file_counts"] == {"pending": 10, "in_progress": 0, "verified": 0, "failed": 0, "uncertain": 0}
    assert job["executor"] == "engineer1"
    assert job["targets"] == [
        {"eqp": "4CCD6701", "may_create_class": False},
        {"eqp": "6MCDE305", "may_create_class": False},
    ]
    assert job["recipes"] == [{"idw": "W1", "idp": "P1"}, {"idw": "W1", "idp": "P2"}]
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
