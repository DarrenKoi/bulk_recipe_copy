import pandas as pd
import pytest

from backend import create_app
from backend.roster import Tool, tools_from_dataframe

ROSTER = [
    Tool("6MCDE305", "192.0.2.12", "M16A", "CG6380", "HITACHI"),
    Tool("6MVRT001", "192.0.2.41", "M16A", "VERITYSEM10", "AMAT"),
    Tool("4CCD6701", "192.0.2.21", "M14", "CG5000", "HITACHI"),
]


def make_client(tmp_path, roster=lambda: ROSTER, spa_dir=None):
    app = create_app(
        roster=roster,
        connect=lambda tool: None,
        db_path=tmp_path / "brc.sqlite3",
        snapshot_dir=tmp_path / "snapshots",
        allowed_users={"engineer1"},
        spa_dir=spa_dir or tmp_path / "no-spa",
    )
    return app.test_client()


def test_api_without_lastuser_cookie_is_401(tmp_path):
    client = make_client(tmp_path)
    assert client.get("/api/me").status_code == 401
    assert client.get("/api/tools").status_code == 401


def test_blank_lastuser_cookie_counts_as_missing(tmp_path):
    client = make_client(tmp_path)
    client.set_cookie("LASTUSER", "  ")
    assert client.get("/api/me").status_code == 401


def test_user_outside_allowlist_is_told_so_and_refused_data(tmp_path):
    client = make_client(tmp_path)
    client.set_cookie("LASTUSER", "stranger")
    me = client.get("/api/me")
    assert me.status_code == 200
    assert me.json == {"user": "stranger", "allowed": False}
    tools = client.get("/api/tools")
    assert tools.status_code == 403
    assert tools.json["error"]["code"] == "forbidden"


def test_allowed_user_sees_hitachi_tools_only_sorted_by_eqp_id(tmp_path):
    client = make_client(tmp_path)
    client.set_cookie("LASTUSER", "engineer1")
    assert client.get("/api/me").json == {"user": "engineer1", "allowed": True}
    assert client.get("/api/tools").json == {
        "tools": [
            {"eqp_id": "4CCD6701", "fab_name": "M14", "model": "CG5000"},
            {"eqp_id": "6MCDE305", "fab_name": "M16A", "model": "CG6380"},
        ]
    }


def test_unreachable_roster_is_502_with_reason(tmp_path):
    def broken():
        raise RuntimeError("office Redis unreachable: ConnectionError")

    client = make_client(tmp_path, roster=broken)
    client.set_cookie("LASTUSER", "engineer1")
    resp = client.get("/api/tools")
    assert resp.status_code == 502
    assert resp.json["error"]["message"] == "office Redis unreachable: ConnectionError"


def test_spa_serves_index_for_client_routes_but_not_for_api(tmp_path):
    spa = tmp_path / "spa"
    spa.mkdir()
    (spa / "index.html").write_text("<html>brc</html>")
    client = make_client(tmp_path, spa_dir=spa)
    assert client.get("/").data == b"<html>brc</html>"
    assert client.get("/tools/6MCDE305").data == b"<html>brc</html>"
    client.set_cookie("LASTUSER", "engineer1")
    assert client.get("/api/nope").status_code == 404


def test_office_roster_rows_normalize_and_reject_unknown_vendor():
    df = pd.DataFrame(
        {
            "eqp_id": [b"6MCDE305", "6MVRT001"],
            "eqp_ip": ["192.0.2.12", None],
            "fab_name": ["M16A", "M16A"],
            "eqp_model_cd": ["CG6380", "VERITYSEM10"],
            "vendor_nm": ["hitachi", "AMAT"],
        }
    )
    assert tools_from_dataframe(df) == [
        Tool("6MCDE305", "192.0.2.12", "M16A", "CG6380", "HITACHI"),
        Tool("6MVRT001", "", "M16A", "VERITYSEM10", "AMAT"),
    ]
    df.loc[1, "vendor_nm"] = "KLA"
    with pytest.raises(LookupError):
        tools_from_dataframe(df)
