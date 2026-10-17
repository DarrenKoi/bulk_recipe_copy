from datetime import datetime

import pytest

from backend import create_app, layout
from backend.browse import HitachiFtpClient
from backend.fake_fleet import KST, FakeTool
from backend.roster import Tool

ROSTER = [
    Tool("6MCDE301", "192.0.2.11", "M16A", "CG6300", "HITACHI"),
    Tool("6MCDE305", "192.0.2.12", "M16A", "CG6380", "HITACHI"),
    Tool("6MVRT001", "192.0.2.41", "M16A", "VERITYSEM10", "AMAT"),
]
T1 = datetime(2026, 10, 18, 9, 30, tzinfo=KST)
T2 = datetime(2026, 10, 17, 14, 5, tzinfo=KST)


@pytest.fixture
def fleet():
    src = FakeTool()
    src.add_recipe("ABC_GATE", "W1", "P1", {"a.tif": b"12345", "sub/b.csv": b"xyz"}, T1)
    src.add_recipe("ABC_GATE", "W1", "P2", {}, T2)
    src.add_recipe("ABC_GATE", "W2", "P1", {"c.tif": b"1"}, T2)
    src.add(layout.idw_path("ABC_GATE", "W1"), b"IDW", T2)
    src.dirs.add(layout.data_dir("EMPTY_CLASS"))
    return {"6MCDE301": src, "6MCDE305": FakeTool()}


@pytest.fixture
def make_client(tmp_path, fleet):
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
        return c

    return make


@pytest.fixture
def client(make_client):
    return make_client()


def test_layout_assembles_the_fixed_recipe_paths():
    assert layout.idp_path("C", "W", "P") == "/HITACHI/DEVICE/HD/C/data/W/P.idp"
    assert layout.raw_dir("C", "W", "P") == "/HITACHI/DEVICE/HD/C/data/W/P"
    assert layout.idw_path("C", "W") == "/HITACHI/DEVICE/HD/C/data/W.idw"
    for bad in ("", "..", ".", "a/b", "a\\b"):
        with pytest.raises(ValueError):
            layout.raw_dir("C", "W", bad)


def test_classes_are_the_folders_under_the_hitachi_root(client):
    resp = client.get("/api/tools/6MCDE301/classes")
    assert resp.status_code == 200
    assert resp.json == {"classes": ["ABC_GATE", "EMPTY_CLASS"]}


def test_recipes_list_every_idp_with_its_idw_and_modified_time(client):
    resp = client.get("/api/tools/6MCDE301/classes/ABC_GATE/recipes")
    assert resp.status_code == 200
    assert resp.json == {
        "recipes": [
            {"idw": "W1", "idp": "P1", "modified": "2026-10-18T09:30:00+09:00"},
            {"idw": "W1", "idp": "P2", "modified": "2026-10-17T14:05:00+09:00"},
            {"idw": "W2", "idp": "P1", "modified": "2026-10-17T14:05:00+09:00"},
        ]
    }


def test_class_without_recipes_is_an_empty_list(client):
    assert client.get("/api/tools/6MCDE301/classes/EMPTY_CLASS/recipes").json == {"recipes": []}


def test_recipe_detail_counts_raw_files_including_subfolders(client):
    resp = client.get("/api/tools/6MCDE301/classes/ABC_GATE/recipes/W1/P1")
    assert resp.status_code == 200
    assert resp.json == {"idw": "W1", "idp": "P1", "file_count": 2, "total_bytes": 8}
    empty = client.get("/api/tools/6MCDE301/classes/ABC_GATE/recipes/W1/P2")
    assert empty.json == {"idw": "W1", "idp": "P2", "file_count": 0, "total_bytes": 0}


@pytest.mark.parametrize(
    "path",
    [
        "/api/tools/6MCDE301/classes",
        "/api/tools/6MCDE301/classes/ABC_GATE/recipes",
        "/api/tools/6MCDE301/classes/ABC_GATE/recipes/W1/P1",
    ],
)
def test_unreachable_tool_is_502_with_reason_not_an_empty_list(client, fleet, path):
    fleet["6MCDE301"].reachable = False
    resp = client.get(path)
    assert resp.status_code == 502
    assert resp.json["error"]["code"] == "tool_unreachable"
    assert "6MCDE301" in resp.json["error"]["message"]
    assert "Connection refused" in resp.json["error"]["message"]


def test_tool_without_hitachi_root_is_502_not_an_empty_class_list(client):
    resp = client.get("/api/tools/6MCDE305/classes")
    assert resp.status_code == 502
    assert "550" in resp.json["error"]["message"]


def test_unknown_or_non_hitachi_tool_is_404(client):
    assert client.get("/api/tools/NOPE/classes").status_code == 404
    assert client.get("/api/tools/6MVRT001/classes").status_code == 404


def test_dot_dot_path_component_is_refused(client):
    resp = client.get("/api/tools/6MCDE301/classes/ABC_GATE/recipes/W1/..")
    assert resp.status_code == 400
    assert resp.json["error"]["code"] == "bad_path"


def test_stalled_listing_is_502_browse_timeout(make_client, fleet):
    fleet["6MCDE301"].stalled = True
    resp = make_client(browse_seconds=3).get("/api/tools/6MCDE301/classes")
    assert resp.status_code == 502
    assert resp.json == {
        "error": {"code": "browse_timeout", "message": "6MCDE301: 탐색이 3초 안에 끝나지 않았습니다."}
    }


def test_entry_budget_is_shared_by_every_listing_of_one_browse(make_client):
    # raw 폴더 최상위가 2개(a.tif, sub)라 예산 2를 다 쓰고, sub 를 읽기 전에 멈춥니다.
    c = make_client(browse_max_entries=2)
    resp = c.get("/api/tools/6MCDE301/classes/ABC_GATE/recipes/W1/P1")
    assert resp.status_code == 502
    assert resp.json == {
        "error": {"code": "browse_too_large", "message": "6MCDE301: 탐색할 항목이 2개를 넘습니다."}
    }
    # 다음 요청은 새 예산으로 시작합니다.
    assert c.get("/api/tools/6MCDE301/classes").json == {"classes": ["ABC_GATE", "EMPTY_CLASS"]}


def test_undecided_recipe_list_command_is_501_with_reason(make_client, fleet):
    class Undecided(FakeTool):
        list_idp_files = HitachiFtpClient.list_idp_files

    fleet["6MCDE301"] = Undecided()
    resp = make_client().get("/api/tools/6MCDE301/classes/ABC_GATE/recipes")
    assert resp.status_code == 501
    assert resp.json == {
        "error": {"code": "not_implemented", "message": "레시피 목록 명령 미확정, 티켓 13"}
    }


def test_entry_budget_counts_every_recipe_the_listing_returns(make_client):
    # 레시피 3개(W1/P1, W1/P2, W2/P1)는 예산 2를 넘습니다. W1 폴더의 LIST가 4줄(P1/, P2/, P1.idp, P2.idp)이라 예산 4면 들어갑니다.
    resp = make_client(browse_max_entries=2).get("/api/tools/6MCDE301/classes/ABC_GATE/recipes")
    assert resp.status_code == 502
    assert resp.json["error"]["code"] == "browse_too_large"
    resp = make_client(browse_max_entries=4).get("/api/tools/6MCDE301/classes/ABC_GATE/recipes")
    assert [(r["idw"], r["idp"]) for r in resp.json["recipes"]] == [("W1", "P1"), ("W1", "P2"), ("W2", "P1")]


def test_a_browse_that_uses_the_budget_exactly_succeeds(make_client, fleet):
    # 항목은 빈 하위 폴더 하나뿐이라 예산 1로 끝나야 합니다. 그 빈 폴더의 LIST는 남은 예산 0으로 돕니다.
    fleet["6MCDE301"].dirs.add(layout.raw_dir("ABC_GATE", "W1", "P2") + "/empty")
    resp = make_client(browse_max_entries=1).get("/api/tools/6MCDE301/classes/ABC_GATE/recipes/W1/P2")
    assert resp.status_code == 200
    assert resp.json["file_count"] == 0
