"""Flask 앱 팩토리. 이후 모든 티켓의 테스트가 이 함수 위에서 돕니다."""

from collections.abc import Callable, Collection
from pathlib import Path

from flask import Flask, g, jsonify, request

from .roster import Tool, hitachi_tools
from .spa import register_spa

# skewnono backend/_auth/provider.py 와 같은 순서: LASTUSER 우선, LAST_USER 는 옛 표기.
_IDENTITY_COOKIES = ("LASTUSER", "LAST_USER")

DEFAULT_SPA_DIR = Path(__file__).resolve().parents[1] / "frontend" / ".output" / "public"


def error_json(code: str, message: str, status: int):
    return jsonify({"error": {"code": code, "message": message}}), status


def read_identity_cookie() -> str | None:
    # 빈 값은 없음으로 봅니다. 빈 문자열이 사용자 id로 흘러가면 안 됩니다.
    for name in _IDENTITY_COOKIES:
        value = (request.cookies.get(name) or "").strip()
        if value:
            return value
    return None


def create_app(
    *,
    roster: Callable[[], list[Tool]],
    connect: Callable[[Tool], object],
    db_path: Path,
    snapshot_dir: Path,
    allowed_users: Collection[str],
    dev_user: str | None = None,
    spa_dir: Path = DEFAULT_SPA_DIR,
) -> Flask:
    """`dev_user` 는 쿠키가 없을 때 쓸 신원입니다. 집 개발 실행만 넣고 운영은 None."""
    app = Flask(__name__)
    db_path = Path(db_path)
    snapshot_dir = Path(snapshot_dir)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    app.config.update(
        ROSTER=roster,
        CONNECT=connect,
        DB_PATH=db_path,
        SNAPSHOT_DIR=snapshot_dir,
        ALLOWED_USERS=frozenset(allowed_users),
    )

    @app.before_request
    def _gate():
        # SPA 파일은 막지 않습니다. 화면이 떠야 막힌 이유를 보여 줄 수 있습니다.
        if not request.path.startswith("/api/"):
            return None
        user = read_identity_cookie() or dev_user
        if not user:
            return error_json("unauthenticated", "LASTUSER 쿠키가 없습니다.", 401)
        g.user = user
        g.allowed = user in app.config["ALLOWED_USERS"]
        if not g.allowed and request.path != "/api/me":
            return error_json("forbidden", "허용 목록에 없는 사용자입니다.", 403)
        return None

    @app.get("/api/me")
    def me():
        return {"user": g.user, "allowed": g.allowed}

    @app.get("/api/tools")
    def tools():
        try:
            rows = hitachi_tools(app.config["ROSTER"]())
        except (LookupError, RuntimeError) as exc:
            return error_json("roster_unavailable", str(exc), 502)
        return {
            "tools": [
                {"eqp_id": t.eqp_id, "fab_name": t.fab_name, "model": t.model} for t in rows
            ]
        }

    register_spa(app, Path(spa_dir))
    return app
