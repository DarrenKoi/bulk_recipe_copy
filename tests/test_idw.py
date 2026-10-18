from test_transfer import T, counts, fleet, make, recipe_files, start  # noqa: F401

from backend import layout
from backend.jobs import run_until_idle

IDW = layout.idw_path("ABC_GATE", "W1")
TARGETS = ("6MCDE305", "4CCD6701")


def test_by_default_no_idw_reaches_any_target(make, fleet):
    app, c = make()
    job_id = start(c)
    run_until_idle(app)

    job = c.get(f"/api/jobs/{job_id}").json
    assert job["include_idw"] is False
    assert job["file_counts"] == counts(verified=10)
    assert all(IDW not in fleet[e].files for e in TARGETS)


def test_with_idw_each_target_gets_the_shared_idw_once_with_the_source_bytes(make, fleet):
    app, c = make()
    job_id = start(c, include_idw=True)
    run_until_idle(app)

    job = c.get(f"/api/jobs/{job_id}").json
    assert job["include_idw"] is True
    # 레시피 2개 × 대상 2대가 같은 W1.idw 를 쓰지만 대상마다 file 하나: 5 + 1 = 6 × 2
    assert job["file_counts"] == counts(verified=12)
    assert job["manifest"]["file_count"] == 6
    for e in TARGETS:
        assert recipe_files(fleet[e])[IDW] == b"IDW"


def test_the_idw_shows_under_every_recipe_using_it_but_counts_once(make, fleet):
    app, c = make()
    job_id = start(c, targets=("6MCDE305",), include_idw=True)
    run_until_idle(app)

    (target,) = c.get(f"/api/jobs/{job_id}").json["tree"]
    assert target["counts"] == counts(verified=6)
    idw_rows = [[f for f in r["files"] if f["path"] == IDW] for r in target["recipes"]]
    assert idw_rows == [[{"path": IDW, "state": "verified", "reason": None, "temp_path": None}]] * 2


def test_an_existing_idw_is_replaced_when_any_pair_using_it_may_overwrite(make, fleet):
    fleet["6MCDE305"].add(IDW, b"OLD IDW", T)
    app, c = make()
    job_id = start(c, targets=("6MCDE305",), include_idw=True,
                   overwrite=[{"eqp": "6MCDE305", "idw": "W1", "idp": "P2"}])
    run_until_idle(app)

    assert c.get(f"/api/jobs/{job_id}").json["file_counts"] == counts(verified=6)
    assert recipe_files(fleet["6MCDE305"])[IDW] == b"IDW"


def test_an_idw_that_appeared_without_permission_stays_and_only_that_file_fails(make, fleet):
    fleet["6MCDE305"].add(IDW, b"OLD IDW", T)
    app, c = make()
    job_id = start(c, targets=("6MCDE305",), include_idw=True)
    run_until_idle(app)

    job = c.get(f"/api/jobs/{job_id}").json
    assert job["file_counts"] == counts(verified=5, failed=1)
    assert job["problems"] == [
        {"eqp": "6MCDE305", "path": IDW, "state": "failed", "reason": "확인 뒤 대상에 생김", "temp_path": None}
    ]
    assert recipe_files(fleet["6MCDE305"])[IDW] == b"OLD IDW"


def test_a_requested_idw_missing_on_the_source_blocks_the_job(make, fleet):
    del fleet["6MCDE301"].files[IDW]
    app, c = make()
    job_id = start(c, include_idw=True)
    run_until_idle(app)

    assert c.get(f"/api/jobs/{job_id}").json["collection"] == {
        "state": "blocked", "reason": f"수집 오류: idw 파일이 없습니다: {IDW}"}


def test_preflight_reports_an_existing_idw_only_when_asked(make, fleet):
    fleet["6MCDE305"].add(IDW, b"OLD IDW", T)
    _, c = make()
    sel = {"source_eqp": "6MCDE301", "class_name": "ABC_GATE",
           "recipes": [{"idw": "W1", "idp": "P1"}], "targets": list(TARGETS)}

    on = c.post("/api/preflight", json=sel | {"include_idw": True}).json["targets"]
    off = c.post("/api/preflight", json=sel).json["targets"]

    assert [t["idw_exists"] for t in on] == [["W1"], []]
    assert ["idw_exists" in t for t in off] == [False, False]


def test_a_non_boolean_include_idw_is_refused(make):
    _, c = make()
    resp = c.post("/api/jobs", json={"source_eqp": "6MCDE301", "class_name": "ABC_GATE",
                                     "recipes": [{"idw": "W1", "idp": "P1"}], "targets": ["6MCDE305"],
                                     "include_idw": "yes"})
    assert (resp.status_code, resp.json["error"]["code"]) == (400, "bad_request")
