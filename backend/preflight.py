"""사전 점검: 대상마다 읽기만 해서 확인 팝업의 세 항목을 돌려줍니다(스펙 "사전 점검과 확인")."""

from concurrent.futures import ThreadPoolExecutor

from flask import Flask, request

from . import layout
from .browse import bounded, read_failure
from .jobs import _Bad, selection


def _names(ftp, remote_dir: str, dirs_only: bool = False) -> set[str]:
    return {e.name for e in ftp.list_details(remote_dir) if e.is_dir or not dirs_only}


def check_target(ftp, class_name: str, recipes: list[tuple[str, str]]) -> dict:
    """폴더는 상위 목록에서 이름을 본 뒤에만 엽니다. 그래서 550 을 "없음"으로 읽을 일이 없습니다."""
    class_exists = class_name in _names(ftp, layout.ROOT, dirs_only=True)
    data_exists = class_exists and "data" in _names(ftp, layout.class_dir(class_name), dirs_only=True)
    existing = []
    if data_exists:
        idws = _names(ftp, layout.data_dir(class_name), dirs_only=True)
        listed: dict[str, set[str]] = {}
        for idw, idp in recipes:
            if idw not in idws:
                continue
            if idw not in listed:
                listed[idw] = _names(ftp, layout.idw_dir(class_name, idw))
            if f"{idp}.idp" in listed[idw] or idp in listed[idw]:
                existing.append({"idw": idw, "idp": idp})
    return {"class_exists": class_exists, "class_missing": not data_exists, "recipe_exists": existing}


def register_preflight(app: Flask) -> None:
    from . import error_json

    @app.post("/api/preflight")
    def preflight():
        try:
            sel = selection(request.get_json(silent=True), app)
        except _Bad as exc:
            return error_json(exc.code, str(exc), exc.status)
        tools, source_fab = sel["tools"], sel["tools"][sel["source_eqp"]].fab_name

        def one(eqp: str) -> dict:
            item = {"eqp": eqp, "fab_name": tools[eqp].fab_name}
            try:
                with app.config["CONNECT"](tools[eqp]) as ftp:
                    found = check_target(bounded(app, ftp), sel["class_name"], sel["recipes"])
            except Exception as exc:
                # 읽지 못한 대상은 "없음"이 아니라 오류 항목입니다.
                if (failure := read_failure(app, eqp, exc)) is None:
                    app.logger.exception("preflight %s failed", eqp)
                    failure = ("preflight_failed", f"{eqp}: {type(exc).__name__}", 502)
                return item | {"error": {"code": failure[0], "message": failure[1]}}
            other_fab = found.pop("class_exists") and item["fab_name"] != source_fab
            return item | found | {"class_exists_other_fab": other_fab}

        # 대상마다 연결 하나로 동시에 읽습니다. 대상 수는 selection 이 스레드 상한으로 막았습니다.
        with ThreadPoolExecutor(max_workers=len(sel["targets"])) as pool:
            return {"source_fab": source_fab, "targets": list(pool.map(one, sel["targets"]))}
