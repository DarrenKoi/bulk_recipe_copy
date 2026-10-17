"""job 생성(신뢰 경계 검사), 스냅샷 수집, 스케줄러, job 조회 API."""

import hashlib
import shutil
import sqlite3
import threading
import time
from pathlib import Path

from flask import Flask, g, request

from ftp_handler.core.client import TransferTooLarge

from . import layout, store
from .browse import walk_files
from .roster import hitachi_tools


def manifest_hash(manifest: list[tuple[str, int, str]]) -> str:
    """(경로, 크기, SHA-256)을 경로순으로. mtime·로컬 폴더 이름은 넣지 않습니다."""
    lines = "".join(f"{p}\t{size}\t{sha}\n" for p, size, sha in sorted(manifest))
    return hashlib.sha256(lines.encode()).hexdigest()


def snapshot_bytes(root: Path) -> int:
    return sum(f.stat().st_size for f in root.rglob("*") if f.is_file())


def _recipe_paths(ftp, class_name: str, idw: str, idp: str):
    # include_idw 는 아직 늘 0 입니다. `.idw` 는 티켓 07.
    yield layout.idp_path(class_name, idw, idp)
    raw = layout.raw_dir(class_name, idw, idp)
    for f in walk_files(ftp, raw):
        if not f.path.startswith(raw + "/"):
            raise ValueError(f"레시피 범위 밖의 경로입니다: {f.path!r}")
        yield f.path


class _Blocked(Exception):
    """메시지가 그대로 공개 차단 사유가 되는 수집 오류."""


def _save(app: Flask, snap: Path, path: str, data: bytes) -> None:
    local = (snap / path.lstrip("/")).resolve()
    if not local.is_relative_to(snap.resolve()):
        raise _Blocked(f"수집 오류: 스냅샷 폴더 밖의 경로입니다: {path!r}")
    try:
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(data)
    except OSError as exc:
        # 예외 원문에는 로컬 경로가 들어 있어 서버 로그에만 남깁니다(스펙 "권한").
        app.logger.exception("snapshot write failed: %s", path)
        raise _Blocked(f"수집 오류: 로컬 저장 실패({type(exc).__name__})") from exc


def _block(db, job_id: int, reason: str) -> None:
    # 완료 기록이 잠금으로 실패했다면 차단 기록도 잠깐 기다렸다 다시 시도합니다.
    for attempt in range(3):
        try:
            return store.block_collection(db, job_id, "collecting", reason)
        except sqlite3.OperationalError:
            if attempt == 2:
                raise
            time.sleep(1)


def collect(app: Flask, job: dict) -> None:
    """원본을 연결 하나로 job 스냅샷 폴더에 받고 manifest 를 확정합니다. 실패하면 차단."""
    db, root = app.config["DB_PATH"], app.config["SNAPSHOT_DIR"]
    snap = root / str(job["id"])
    shutil.rmtree(snap, ignore_errors=True)
    recipes = sorted({(idw, idp) for _, idw, idp in job["pairs"]})
    manifest, recipe_paths = [], {}
    try:
        cap = app.config["SNAPSHOT_CAP_BYTES"]
        try:
            left = cap - (snapshot_bytes(root) if root.exists() else 0)
        except OSError as exc:
            app.logger.exception("snapshot size check failed")
            raise _Blocked(f"수집 오류: 로컬 저장 실패({type(exc).__name__})") from exc
        tools = {t.eqp_id: t for t in hitachi_tools(app.config["ROSTER"]())}
        if job["source_eqp"] not in tools:
            raise LookupError(f"명부에 없는 Hitachi 장비입니다: {job['source_eqp']}")
        with app.config["CONNECT"](tools[job["source_eqp"]]) as ftp:
            for idw, idp in recipes:
                paths = recipe_paths[(idw, idp)] = []
                for path in _recipe_paths(ftp, job["class_name"], idw, idp):
                    try:
                        data = ftp.download(path, max_bytes=max(left, 0))
                    except TransferTooLarge:
                        raise _Blocked(f"수집 오류: 스냅샷 총량 상한 {cap} B를 넘습니다.") from None
                    left -= len(data)
                    _save(app, snap, path, data)
                    paths.append(path)
                    manifest.append((path, len(data), hashlib.sha256(data).hexdigest()))
        store.finish_collection(
            db, job["id"], manifest=manifest, manifest_hash=manifest_hash(manifest),
            snapshot_dir=snap, pairs=job["pairs"], recipe_paths=recipe_paths,
        )
    except Exception as exc:  # 어떤 오류든 job 이 '수집 중'에 머물지 않고 사유와 함께 끝나야 합니다.
        app.logger.warning("collection of job %s blocked", job["id"], exc_info=True)
        shutil.rmtree(snap, ignore_errors=True)
        reason = str(exc) if isinstance(exc, _Blocked) else f"수집 오류: {type(exc).__name__}: {exc}"
        _block(db, job["id"], reason)


def run_until_idle(app: Flask) -> None:
    """대기 job 을 id 순으로 하나씩 처리합니다. 스케줄러 스레드와 테스트가 같이 씁니다."""
    while (job := store.claim_next_waiting(app.config["DB_PATH"])) is not None:
        collect(app, job)


def start_scheduler(app: Flask) -> None:
    # ponytail: 한 번에 job 하나. 장비 겹침·스레드 예산 판정은 티켓 10.
    wake = app.config["SCHEDULER_WAKE"] = threading.Event()

    def loop():
        while True:
            try:
                run_until_idle(app)
            except Exception:
                app.logger.exception("scheduler pass failed")
            wake.wait(5)
            wake.clear()

    threading.Thread(target=loop, name="brc-scheduler", daemon=True).start()


class _Bad(Exception):
    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code, self.status = code, status


def _strings(value, what: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise _Bad("bad_request", f"{what}는 문자열 목록이어야 합니다.")
    return value


def _parse(body, app: Flask) -> dict:
    if not isinstance(body, dict):
        raise _Bad("bad_request", "JSON 객체가 필요합니다.")
    source, class_name = body.get("source_eqp"), body.get("class_name")
    if not isinstance(source, str) or not isinstance(class_name, str):
        raise _Bad("bad_request", "source_eqp, class_name 은 문자열이어야 합니다.")
    raw_recipes = body.get("recipes")
    if not isinstance(raw_recipes, list) or not all(isinstance(r, dict) for r in raw_recipes):
        raise _Bad("bad_request", "recipes 는 {idw, idp} 목록이어야 합니다.")
    pairs = [(r.get("idw"), r.get("idp")) for r in raw_recipes]
    if not all(isinstance(w, str) and isinstance(p, str) for w, p in pairs):
        raise _Bad("bad_request", "idw, idp 는 문자열이어야 합니다.")
    recipes = list(dict.fromkeys(pairs))
    targets = list(dict.fromkeys(_strings(body.get("targets"), "targets")))
    if not recipes:
        raise _Bad("no_recipes", "레시피를 하나 이상 골라야 합니다.")
    if not targets:
        raise _Bad("no_targets", "대상 장비를 하나 이상 골라야 합니다.")

    try:
        layout.class_dir(class_name)
        for idw, idp in recipes:
            layout.idp_path(class_name, idw, idp)
            layout.raw_dir(class_name, idw, idp)
    except ValueError as exc:
        raise _Bad("bad_path", str(exc)) from None

    try:
        tools = {t.eqp_id for t in hitachi_tools(app.config["ROSTER"]())}
    except (LookupError, RuntimeError) as exc:
        raise _Bad("roster_unavailable", str(exc), 502) from None
    unknown = [e for e in [source, *targets] if e not in tools]
    if unknown:
        raise _Bad("unknown_tool", f"명부에 없는 Hitachi 장비입니다: {', '.join(unknown)}")
    if source in targets:
        raise _Bad("source_in_targets", f"원본 장비 {source} 는 대상이 될 수 없습니다.")

    cap = app.config["MAX_THREADS"]
    if len(targets) > cap:
        raise _Bad("too_many_targets", f"대상 {len(targets)}대가 스레드 상한 {cap}을 넘습니다.")
    # 새 job 의 크기는 수집 전이라 모릅니다. 수집 중에도 남은 총량으로 다시 막습니다(collect).
    used, limit = snapshot_bytes(app.config["SNAPSHOT_DIR"]), app.config["SNAPSHOT_CAP_BYTES"]
    if used >= limit:
        raise _Bad("snapshot_full", f"스냅샷 총량 {used} B가 상한 {limit} B에 닿아 새 job 을 만들 수 없습니다.", 507)
    # 티켓 04 의 요청은 대상 × 레시피 전부입니다. 쌍을 빼는 확인 단계는 티켓 06.
    pairs = [(eqp, idw, idp) for eqp in targets for idw, idp in recipes]
    return {"source_eqp": source, "class_name": class_name, "pairs": pairs}


def register_jobs(app: Flask) -> None:
    from . import error_json

    @app.post("/api/jobs")
    def create_job():
        try:
            spec = _parse(request.get_json(silent=True), app)
        except _Bad as exc:
            return error_json(exc.code, str(exc), exc.status)
        job_id = store.create_job(app.config["DB_PATH"], executor=g.user, **spec)
        if wake := app.config.get("SCHEDULER_WAKE"):
            wake.set()
        return store.get_job(app.config["DB_PATH"], job_id), 201

    @app.get("/api/jobs")
    def list_jobs():
        return {"jobs": store.list_jobs(app.config["DB_PATH"])}

    @app.get("/api/jobs/<int:job_id>")
    def get_job(job_id):
        job = store.get_job(app.config["DB_PATH"], job_id)
        if job is None:
            return error_json("not_found", f"job {job_id} 이 없습니다.", 404)
        return job
