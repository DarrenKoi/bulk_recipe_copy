"""원본 탐색 API: class 목록, 레시피 목록, raw 폴더 요약."""

import ftplib
import time

from flask import Flask

from ftp_handler.core.client import FtpClient, ListingTooLarge, TransferTimeout

from . import layout
from .roster import hitachi_tools


class HitachiFtpClient(FtpClient):
    def list_idp_files(self, data_dir: str, **limits):
        # 스펙은 LIST 1회. Hitachi 서버의 명령·응답 형식이 OFFICE-VERIFY 라 티켓 13에서 구현합니다.
        # idw 폴더마다 LIST(N+1)로 바꾸는 것은 사용자가 정할 일이라 폴백을 넣지 않습니다.
        raise NotImplementedError("레시피 목록 명령 미확정, 티켓 13")


class Bounded:
    """탐색 요청 하나의 LIST들이 시간·항목 예산 하나를 나눠 씁니다.

    LIST마다 따로 상한을 두면 큰 하위 트리나 조금씩 응답하는 장비가 요청 스레드를 계속 잡습니다.
    """

    def __init__(self, ftp, seconds: float, entries: int) -> None:
        self._ftp, self._end, self._entries = ftp, time.monotonic() + seconds, entries
        self.seconds, self.max_entries = seconds, entries

    def _limits(self) -> dict:
        left = self._end - time.monotonic()
        if left <= 0:
            raise TransferTimeout(f"browse exceeded {self.seconds}s")
        return {"deadline": left, "max_entries": self._entries}

    def _spend(self, entries: list) -> list:
        if len(entries) > self._entries:
            raise ListingTooLarge(f"browse has over {self.max_entries} entries")
        self._entries -= len(entries)
        return entries

    def list_details(self, remote_dir: str, pattern: str | None = None):
        return self._spend(self._ftp.list_details(remote_dir, pattern, **self._limits()))

    def list_idp_files(self, data_dir: str):
        return self._spend(self._ftp.list_idp_files(data_dir, **self._limits()))


def walk_files(ftp, remote_dir: str):
    """`remote_dir` 아래 파일 전부(하위 폴더 포함). 링크는 따라가지 않고 건너뜁니다.

    목록은 장비가 준 데이터라 항목마다 요청한 폴더의 직계 자식인지 확인합니다(`../x` 같은 이름 거부).
    재귀 대신 스택이라 깊은 폴더 체인도 RecursionError 없이 `Bounded` 예산에서 멈춥니다.
    """
    stack = [remote_dir]
    while stack:
        d = stack.pop()
        for e in ftp.list_details(d):
            if e.path != layout.child(d, e.name):
                raise ValueError(f"{d} 의 직계 자식이 아닌 목록 항목입니다: {e.path!r}")
            if e.is_link:
                continue
            if e.is_dir:
                stack.append(e.path)
            else:
                yield e


def _raw_summary(ftp, remote_dir: str) -> tuple[int, int]:
    files = list(walk_files(ftp, remote_dir))
    return len(files), sum(f.size or 0 for f in files)


def _file_size(ftp, remote_dir: str, name: str) -> int | None:
    return next((e.size for e in ftp.list_details(remote_dir) if e.name == name and not e.is_dir), None)


def bounded(app: Flask, ftp) -> Bounded:
    return Bounded(ftp, app.config["BROWSE_SECONDS"], app.config["BROWSE_MAX_ENTRIES"])


def read_failure(app: Flask, eqp: str, exc: Exception) -> tuple[str, str, int] | None:
    """읽기 전용 조회의 예외 -> (code, message, HTTP status). 모르는 예외면 None."""
    if isinstance(exc, ValueError):
        return "bad_path", str(exc), 400
    if isinstance(exc, NotImplementedError):
        return "not_implemented", str(exc), 501
    if isinstance(exc, TransferTimeout):
        return "browse_timeout", f"{eqp}: 탐색이 {app.config['BROWSE_SECONDS']}초 안에 끝나지 않았습니다.", 502
    if isinstance(exc, ListingTooLarge):
        return "browse_too_large", f"{eqp}: 탐색할 항목이 {app.config['BROWSE_MAX_ENTRIES']}개를 넘습니다.", 502
    if isinstance(exc, (OSError, EOFError, ftplib.Error)):
        # 빈 목록으로 숨기지 않습니다. "레시피 없음"과 "장비에 못 닿음"은 다릅니다.
        return "tool_unreachable", f"{eqp}: {type(exc).__name__}: {exc}", 502
    return None


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
                return op(bounded(app, ftp))
        except Exception as exc:
            if (failure := read_failure(app, eqp, exc)) is None:
                raise
            return error_json(*failure)

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
            # 확인 팝업의 총 크기에 .idp 도 넣으려고 idw 폴더를 한 번 더 읽습니다. 없으면 None.
            idp_bytes = _file_size(ftp, layout.idw_dir(class_name, idw), f"{idp}.idp")
            # include_idw 일 때 쓰려고 data/ 도 한 번 읽어 .idw 크기를 같은 예산 안에서 돌려줍니다. 없으면 None.
            idw_bytes = _file_size(ftp, layout.data_dir(class_name), f"{idw}.idw")
            return {"idw": idw, "idp": idp, "file_count": count, "total_bytes": total,
                    "idp_bytes": idp_bytes, "idw_bytes": idw_bytes}

        return browse(eqp, op)
