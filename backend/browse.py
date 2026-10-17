"""원본 탐색 API: class 목록, 레시피 목록, raw 폴더 요약."""

import ftplib

from flask import Flask

from ftp_handler.core.client import FtpClient

from . import layout
from .roster import hitachi_tools


class HitachiFtpClient(FtpClient):
    def list_idp_files(self, data_dir: str):
        # 스펙은 LIST 1회. Hitachi 서버의 명령·응답 형식이 OFFICE-VERIFY 라 티켓 13에서 구현합니다.
        # idw 폴더마다 LIST(N+1)로 바꾸는 것은 사용자가 정할 일이라 폴백을 넣지 않습니다.
        raise NotImplementedError("레시피 목록 명령은 티켓 13(사무실 회신 G) 뒤에 구현합니다.")


def _raw_summary(ftp, remote_dir: str) -> tuple[int, int]:
    count = total = 0
    for e in ftp.list_details(remote_dir):
        if e.is_link:
            continue
        if e.is_dir:
            c, t = _raw_summary(ftp, e.path)
            count, total = count + c, total + t
        else:
            count, total = count + 1, total + (e.size or 0)
    return count, total


def register_browse(app: Flask) -> None:
    from . import error_json

    def browse(eqp: str, op):
        try:
            tools = {t.eqp_id: t for t in hitachi_tools(app.config["ROSTER"]())}
        except (LookupError, RuntimeError) as exc:
            return error_json("roster_unavailable", str(exc), 502)
        if eqp not in tools:
            return error_json("unknown_tool", f"명부에 없는 Hitachi 장비입니다: {eqp}", 404)
        try:
            with app.config["CONNECT"](tools[eqp]) as ftp:
                return op(ftp)
        except ValueError as exc:
            return error_json("bad_path", str(exc), 400)
        except (OSError, EOFError, ftplib.Error) as exc:
            # 빈 목록으로 숨기지 않습니다. "레시피 없음"과 "장비에 못 닿음"은 다릅니다.
            return error_json("tool_unreachable", f"{eqp}: {type(exc).__name__}: {exc}", 502)

    @app.get("/api/tools/<eqp>/classes")
    def classes(eqp):
        return browse(eqp, lambda ftp: {
            "classes": sorted(e.name for e in ftp.list_details(layout.ROOT) if e.is_dir)
        })

    @app.get("/api/tools/<eqp>/classes/<class_name>/recipes")
    def recipes(eqp, class_name):
        def op(ftp):
            rows = []
            for f in ftp.list_idp_files(layout.data_dir(class_name)):
                idw, idp = layout.recipe_of_idp_path(f.path)
                rows.append({
                    "idw": idw,
                    "idp": idp,
                    "modified": f.modified.isoformat() if f.modified else None,
                })
            return {"recipes": sorted(rows, key=lambda r: (r["idw"], r["idp"]))}

        return browse(eqp, op)

    @app.get("/api/tools/<eqp>/classes/<class_name>/recipes/<idw>/<idp>")
    def recipe(eqp, class_name, idw, idp):
        def op(ftp):
            count, total = _raw_summary(ftp, layout.raw_dir(class_name, idw, idp))
            return {"idw": idw, "idp": idp, "file_count": count, "total_bytes": total}

        return browse(eqp, op)
