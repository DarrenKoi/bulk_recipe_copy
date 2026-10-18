import pytest
from test_transfer import P1_IDP, P1_RAW_DIR, T, counts, fleet, make, recipe_files, source_recipe_files  # noqa: F401

from backend import layout
from backend.jobs import run_until_idle

P2_IDP = layout.idp_path("ABC_GATE", "W1", "P2")
SELECTION = {
    "source_eqp": "6MCDE301",
    "class_name": "ABC_GATE",
    "recipes": [{"idw": "W1", "idp": "P1"}, {"idw": "W1", "idp": "P2"}],
    "targets": ["6MCDE305", "4CCD6701"],
}
P1 = {"idw": "W1", "idp": "P1"}
P2 = {"idw": "W1", "idp": "P2"}


def old_recipes_on(tool) -> None:
    tool.add_recipe("ABC_GATE", "W1", "P1", {"a.tif": b"old-a", "old.tif": b"only-here"}, T)
    tool.add_recipe("ABC_GATE", "W1", "P2", {"c.tif": b"old-c"}, T)


def test_preflight_reports_missing_class_other_fab_class_and_existing_recipes(make, fleet):
    # 4CCD6701 은 다른 fab(M14). P1 은 .idp 로, P2 는 raw 폴더만 있어도 "있음"입니다.
    fleet["4CCD6701"].add(P1_IDP, b"IDP old", T)
    fleet["4CCD6701"].dirs.add(layout.raw_dir("ABC_GATE", "W1", "P2"))
    before = {e: (recipe_files(t), t._all_dirs()) for e, t in fleet.items()}
    _, c = make()

    resp = c.post("/api/preflight", json=SELECTION)

    assert resp.status_code == 200
    assert resp.json == {"source_fab": "M16A", "targets": [
        {"eqp": "6MCDE305", "fab_name": "M16A", "class_missing": True,
         "class_exists_other_fab": False, "recipe_exists": []},
        {"eqp": "4CCD6701", "fab_name": "M14", "class_missing": False,
         "class_exists_other_fab": True, "recipe_exists": [P1, P2]},
    ]}
    assert {e: (recipe_files(t), t._all_dirs()) for e, t in fleet.items()} == before


def test_same_fab_target_with_the_class_is_not_warned(make, fleet):
    fleet["6MCDE305"].dirs.add(layout.data_dir("ABC_GATE"))
    _, c = make()

    resp = c.post("/api/preflight", json=SELECTION | {"targets": ["6MCDE305"]})

    assert resp.json["targets"] == [{"eqp": "6MCDE305", "fab_name": "M16A", "class_missing": False,
                                     "class_exists_other_fab": False, "recipe_exists": []}]


def test_an_unreadable_target_is_an_error_item_not_absent(make, fleet):
    fleet["4CCD6701"].reachable = False
    _, c = make()

    targets = c.post("/api/preflight", json=SELECTION).json["targets"]

    assert targets[0]["class_missing"] is True
    assert targets[1] == {"eqp": "4CCD6701", "fab_name": "M14", "error": {
        "code": "tool_unreachable", "message": "4CCD6701: ConnectionRefusedError: [Errno 61] Connection refused"}}


def test_preflight_rejects_a_bad_selection(make):
    _, c = make()
    resp = c.post("/api/preflight", json=SELECTION | {"targets": ["6MCDE301"]})
    assert (resp.status_code, resp.json["error"]["code"]) == (400, "source_in_targets")


def test_a_permitted_target_gets_the_class_folder_then_the_recipes(make, fleet):
    app, c = make()
    job_id = c.post("/api/jobs", json=SELECTION | {"targets": ["6MCDE305"], "create_class": ["6MCDE305"]}).json["id"]
    run_until_idle(app)

    assert c.get(f"/api/jobs/{job_id}").json["file_counts"] == counts(verified=5)
    assert layout.data_dir("ABC_GATE") in fleet["6MCDE305"]._all_dirs()
    assert recipe_files(fleet["6MCDE305"]) == source_recipe_files(fleet)


def test_a_declined_target_is_left_out_of_the_job_and_untouched(make, fleet):
    # 확인 팝업에서 4CCD6701 의 폴더 생성을 거절하면 그 대상의 쌍이 요청에서 빠집니다.
    app, c = make()
    pairs = [{"eqp": "6MCDE305"} | P1, {"eqp": "6MCDE305"} | P2]
    job = c.post("/api/jobs", json=SELECTION | {"pairs": pairs, "create_class": ["6MCDE305"]}).json
    run_until_idle(app)

    assert job["targets"] == [{"eqp": "6MCDE305", "may_create_class": True}]
    assert c.get(f"/api/jobs/{job['id']}").json["file_counts"] == counts(verified=5)
    assert fleet["4CCD6701"]._all_dirs() == {"/", "/HITACHI", "/HITACHI/DEVICE", layout.ROOT}


def test_only_permitted_pairs_are_replaced_and_the_rest_stay_byte_identical(make, fleet):
    old_recipes_on(fleet["4CCD6701"])
    old = recipe_files(fleet["4CCD6701"])
    app, c = make()
    job = c.post("/api/jobs", json=SELECTION | {
        "targets": ["4CCD6701"], "overwrite": [{"eqp": "4CCD6701"} | P1],
    }).json
    run_until_idle(app)

    assert job["overwrite"] == [{"eqp": "4CCD6701"} | P1]
    after = c.get(f"/api/jobs/{job['id']}").json
    assert after["file_counts"] == counts(verified=3, failed=2)
    assert sorted((p["path"], p["reason"]) for p in after["problems"]) == [
        (P2_IDP, "확인 뒤 대상에 생김"),
        (f"{layout.raw_dir('ABC_GATE', 'W1', 'P2')}/c.tif", "확인 뒤 대상에 생김"),
    ]
    files = recipe_files(fleet["4CCD6701"])
    src = source_recipe_files(fleet)
    assert {p: files[p] for p in src if p.startswith(P1_RAW_DIR) or p == P1_IDP} == {
        p: d for p, d in src.items() if p.startswith(P1_RAW_DIR) or p == P1_IDP}
    assert files[f"{P1_RAW_DIR}/old.tif"] == b"only-here"  # 원본에 없는 파일은 지우지 않습니다
    assert {p: d for p, d in files.items() if "/P2" in p} == {p: d for p, d in old.items() if "/P2" in p}


def test_a_target_without_folder_permission_gets_no_class_folder(make, fleet):
    app, c = make()
    job_id = c.post("/api/jobs", json=SELECTION | {"targets": ["6MCDE305"]}).json["id"]
    run_until_idle(app)

    job = c.get(f"/api/jobs/{job_id}").json
    assert job["file_counts"] == counts(failed=5)
    assert {p["reason"] for p in job["problems"]} == {"대상에 class 폴더 없음"}
    assert fleet["6MCDE305"]._all_dirs() == {"/", "/HITACHI", "/HITACHI/DEVICE", layout.ROOT}


@pytest.mark.parametrize("over, code", [
    ({"pairs": [{"eqp": "6MCDE305", "idw": "W1", "idp": "P9"}]}, "pair_outside_selection"),
    ({"pairs": [{"eqp": "6MCDE402"} | P1]}, "pair_outside_selection"),
    ({"pairs": []}, "no_pairs"),
    ({"pairs": [{"eqp": "6MCDE305"} | P1], "overwrite": [{"eqp": "6MCDE305"} | P2]}, "overwrite_outside_selection"),
    ({"pairs": [{"eqp": "6MCDE305"} | P1], "create_class": ["4CCD6701"]}, "create_class_outside_selection"),
    ({"overwrite": [{"eqp": "6MCDE305", "idw": "W1"}]}, "bad_request"),
    ({"create_class": "6MCDE305"}, "bad_request"),
])
def test_choices_outside_the_jobs_own_selection_are_refused(make, over, code):
    _, c = make()
    resp = c.post("/api/jobs", json=SELECTION | over)
    assert (resp.status_code, resp.json["error"]["code"]) == (400, code)
    assert c.get("/api/jobs").json == {"jobs": []}
