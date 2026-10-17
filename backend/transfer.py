"""장비 작업자: 수집이 끝난 job 을 대상마다 연결 하나로 올리고 재다운로드 SHA-256으로 검증합니다(스펙 "장비 작업자")."""

import hashlib
import posixpath
import threading
from ftplib import Error as FtpError
from ftplib import error_perm
from pathlib import Path

from flask import Flask

from ftp_handler.core.client import TargetExists, TransferTimeout, TransferTooLarge, TransferUncertain

from . import layout, store
from .roster import hitachi_tools

# 재연결해서 같은 파일부터 다시 할 오류. error_perm(5xx)은 장비의 거부라 재시도해도 같습니다.
_CONNECTION_ERRORS = (OSError, EOFError, TransferTimeout, FtpError)
_APPEARED = "확인 뒤 대상에 생김"


class _Discarded(Exception):
    """FtpClient 가 연결을 버린 뒤. 재시도로 세지 않고 다시 연결만 합니다."""


class _SnapshotDamaged(Exception):
    pass


def _names(ftp, remote_dir: str, deadline: float) -> set[str] | None:
    """폴더 항목 이름들. 폴더가 없다고 확인되면 None.

    550 은 권한 거부일 수도 있어 바로 "없음"으로 보지 않고, 상위 목록에 그 이름이 없을 때만 없음입니다.
    `layout.ROOT` 까지 550 이면 조회 실패로 올립니다.
    """
    try:
        return {e.name for e in ftp.list_details(remote_dir, deadline=deadline)}
    except error_perm as exc:
        if not str(exc).startswith("550") or remote_dir == layout.ROOT:
            raise ConnectionError(f"LIST {remote_dir}: {exc}") from exc
        refused = exc
    parent = _names(ftp, posixpath.dirname(remote_dir), deadline)
    if parent is not None and posixpath.basename(remote_dir) in parent:
        raise ConnectionError(f"LIST {remote_dir}: {refused}") from refused
    return None


def _recipe_present(ftp, class_name: str, idw: str, idp: str, deadline: float) -> bool:
    names = _names(ftp, layout.idw_dir(class_name, idw), deadline) or set()
    return f"{idp}.idp" in names or idp in names


class _Worker:
    """대상 장비 하나. 파일은 레시피 순, 레시피 안에선 경로 순으로 하나씩 보냅니다."""

    def __init__(self, app: Flask, plan: dict, eqp: str, tool) -> None:
        self.app, self.plan, self.eqp, self.tool = app, plan, eqp, tool
        self.db, self.job_id = app.config["DB_PATH"], plan["id"]
        self.seconds, self.max_bytes = app.config["TRANSFER_SECONDS"], app.config["TRANSFER_MAX_BYTES"]
        self.checked: set[tuple[str, str]] = set()  # 첫 쓰기 전 재확인을 마친 (idw, idp)
        self.made: set[str] = set()  # 이미 mkdir 한 폴더
        # 이 작업자가 in_progress 로 잡은 파일과, 그 파일의 put 이 끝났는지. 재연결 뒤 같은 파일을
        # 이어 갈 때 다시 잡거나 다시 올리지 않으려고 둡니다(다시 올리면 자기 파일에 TargetExists).
        self.claimed: str | None = None
        self.put_done = False

    def queue(self) -> list[tuple[tuple[str, str], list[str]]]:
        """(레시피, 그 레시피의 남은 경로들)을 파일마다 한 항목씩."""
        cls, pending = self.plan["class_name"], self.plan["pending"].get(self.eqp, [])
        out = []
        for eqp, idw, idp in self.plan["pairs"]:
            if eqp == self.eqp:
                idp_path, raw = layout.idp_path(cls, idw, idp), layout.raw_dir(cls, idw, idp) + "/"
                paths = [p for p in pending if p == idp_path or p.startswith(raw)]
                out += [((idw, idp), paths, p) for p in paths]
        return out

    def run(self) -> None:
        queue, i, failures = self.queue(), 0, 0
        while i < len(queue):
            entered = False
            try:
                with self.app.config["CONNECT"](self.tool) as ftp:
                    entered = True
                    while i < len(queue):
                        self.send(ftp, *queue[i])
                        i, failures = i + 1, 0
            except _Discarded:
                i, failures = i + 1, 0
            except error_perm as exc:
                if not entered:  # 로그인 거부(530 등). 다시 붙어도 같으니 남은 파일을 모두 실패로.
                    store.fail_pending(self.db, self.job_id, f"로그인 거부: {exc}", eqp=self.eqp)
                    return
                # 장비가 거부한 파일 하나. 다음 파일은 새 연결로.
                self._end(queue[i][2], "failed", f"장비 거부: {exc}")
                i, failures = i + 1, 0
            except _CONNECTION_ERRORS:
                self.app.logger.warning("job %s %s: connection error", self.job_id, self.eqp, exc_info=True)
                failures += 1
                if failures > self.app.config["TRANSFER_RETRIES"]:
                    if self.claimed == queue[i][2]:
                        # put 까지 끝났으면 대상에 무엇이 있는지 모릅니다. 근거 없이 실패로 두지 않습니다.
                        self._end(self.claimed, *(("uncertain", "검증 전 연결 실패") if self.put_done
                                                  else ("failed", "연결 실패")))
                    store.fail_pending(self.db, self.job_id, "연결 실패", eqp=self.eqp)
                    return
            except Exception as exc:
                # 예상 못 한 오류. 잡은 파일은 쓰였는지 모르니 uncertain, 나머지는 실패로 끝내
                # job 이 in_progress 에 걸려 끝나지 않는 일을 막습니다.
                self.app.logger.exception("job %s %s: worker crashed", self.job_id, self.eqp)
                reason = f"작업자 오류: {type(exc).__name__}"
                if self.claimed is not None:
                    self._end(self.claimed, "uncertain", reason)
                store.fail_pending(self.db, self.job_id, reason, eqp=self.eqp)
                return

    def _end(self, path: str, to: str, reason: str | None = None, temp_path: str | None = None) -> None:
        store.move_file(self.db, self.job_id, self.eqp, path, "in_progress", to, reason=reason, temp_path=temp_path)

    def send(self, ftp, recipe: tuple[str, str], recipe_paths: list[str], path: str) -> None:
        if path != self.claimed:
            if not store.move_file(self.db, self.job_id, self.eqp, path, "pending", "in_progress"):
                return  # 이미 실패 처리됨(재확인, 스냅샷 손상)
            self.claimed, self.put_done = path, False
        if not self.put_done:
            try:
                data = self._snapshot(path)
            except _SnapshotDamaged:
                self._end(path, "failed", "스냅샷 손상")
                # 같은 스냅샷을 쓰는 job 전체를 멈춥니다. 재수집은 티켓 12.
                store.fail_pending(self.db, self.job_id, "스냅샷 손상")
                return
            if data is None:
                return self._end(path, "failed", "전송 크기 상한 초과")
            overwrite = (self.eqp, *recipe) in self.plan["overwrites"]
            if not overwrite and recipe not in self.checked:
                if _recipe_present(ftp, self.plan["class_name"], *recipe, self.seconds):
                    self._end(path, "failed", _APPEARED)
                    store.fail_pending(self.db, self.job_id, _APPEARED, eqp=self.eqp, paths=recipe_paths)
                    return
                self.checked.add(recipe)
            parent = posixpath.dirname(path)
            if parent not in self.made:
                ftp.mkdir(parent)
                self.made.add(parent)
            try:
                ftp.put(path, data, overwrite=overwrite, deadline=self.seconds)
            except TargetExists:
                return self._end(path, "failed", _APPEARED)
            except TransferUncertain as exc:
                # FtpClient 는 이미 연결을 버렸습니다. 다음 파일은 새 연결로.
                self._end(path, "uncertain", "rename 응답 유실", exc.temp_path)
                raise _Discarded from None
            self.put_done = True
        try:
            got = ftp.download(path, max_bytes=self.max_bytes, deadline=self.seconds)
        except TransferTooLarge:
            self._end(path, "failed", "해시 불일치")
            raise _Discarded from None
        ok = hashlib.sha256(got).hexdigest() == self.plan["manifest"][path]
        self._end(path, *(("verified",) if ok else ("failed", "해시 불일치")))

    def _snapshot(self, path: str) -> bytes | None:
        """스냅샷 바이트. 전송 상한을 넘으면 None(상한 + 1 바이트까지만 읽습니다)."""
        try:
            with open(Path(self.plan["snapshot_dir"]) / path.lstrip("/"), "rb") as f:
                data = f.read(self.max_bytes + 1)
        except OSError:
            raise _SnapshotDamaged from None
        if len(data) > self.max_bytes:
            return None
        if hashlib.sha256(data).hexdigest() != self.plan["manifest"].get(path):
            raise _SnapshotDamaged
        return data


def transfer_job(app: Flask, job_id: int, *, parallel: bool = False) -> None:
    """수집이 끝난 job 의 남은 pending 을 보냅니다. `parallel` 이면 대상마다 스레드(운영), 아니면 순차(테스트)."""
    plan = store.transfer_plan(app.config["DB_PATH"], job_id)
    if plan is None or not plan["pending"]:
        return
    try:
        tools = {t.eqp_id: t for t in hitachi_tools(app.config["ROSTER"]())}
    except (LookupError, RuntimeError):
        app.logger.exception("job %s: roster unavailable", job_id)
        tools = {}
    workers = []
    for eqp in plan["pending"]:
        if eqp not in tools:
            store.fail_pending(app.config["DB_PATH"], job_id, "연결 실패: 명부에 없는 장비", eqp=eqp)
        else:
            workers.append(_Worker(app, plan, eqp, tools[eqp]))
    if not parallel:
        for w in workers:
            w.run()
        return
    threads = [threading.Thread(target=_logged, args=(app, w), name=f"brc-{job_id}-{w.eqp}") for w in workers]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


def _logged(app: Flask, worker: _Worker) -> None:
    try:
        worker.run()
    except Exception:
        # run() 의 정리 자체가 실패한 경우뿐입니다. 남은 in_progress 는 재기동 대조(티켓 12).
        app.logger.exception("job %s %s: worker crashed", worker.job_id, worker.eqp)
