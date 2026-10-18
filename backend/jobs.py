"""job 생성(신뢰 경계 검사), 스냅샷 수집, 스케줄러, job 조회 API."""

import hashlib
import shutil
import sqlite3
import threading
import time
from pathlib import Path

from flask import Flask, g, request

from ftp_handler.core.client import TransferTooLarge

from . import layout, scheduler, store
from .browse import walk_files
from .roster import hitachi_tools
from .transfer import transfer_job


def manifest_hash(manifest: list[tuple[str, int, str]]) -> str:
    """(경로, 크기, SHA-256)을 경로순으로. mtime·로컬 폴더 이름은 넣지 않습니다."""
    lines = "".join(f"{p}\t{size}\t{sha}\n" for p, size, sha in sorted(manifest))
    return hashlib.sha256(lines.encode()).hexdigest()


def snapshot_bytes(root: Path) -> int:
    return sum(f.stat().st_size for f in root.rglob("*") if f.is_file())


class _Blocked(Exception):
    """메시지가 그대로 공개 차단 사유가 되는 수집 오류."""


class _MissingIdp(_Blocked):
    pass


def _recipe_files(ftp, class_name: str, idw: str, idp: str):
    """레시피의 파일 목록(경로·크기·mtime). 받을 파일 목록이자 변경 탐지의 비교 대상입니다."""
    # include_idw 는 아직 늘 0 입니다. `.idw` 는 티켓 07.
    idp_file = layout.idp_path(class_name, idw, idp)
    found = [f for f in ftp.list_details(layout.idw_dir(class_name, idw)) if f.path == idp_file and not f.is_dir]
    if not found:
        raise _MissingIdp(f"수집 오류: 레시피 파일이 없습니다: {idp_file}")
    yield found[0]
    raw = layout.raw_dir(class_name, idw, idp)
    for f in walk_files(ftp, raw):
        if not f.path.startswith(raw + "/"):
            raise ValueError(f"레시피 범위 밖의 경로입니다: {f.path!r}")
        yield f


def _listing(ftp, class_name: str, recipes) -> dict:
    return {
        (idw, idp): sorted((f.path, f.size, f.modified) for f in _recipe_files(ftp, class_name, idw, idp))
        for idw, idp in recipes
    }


def _save(app: Flask, snap: Path, path: str, data: bytes) -> None:
    try:
        local = (snap / path.lstrip("/")).resolve()
        if not local.is_relative_to(snap.resolve()):
            raise _Blocked(f"수집 오류: 스냅샷 폴더 밖의 경로입니다: {path!r}")
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(data)
    except (OSError, RuntimeError) as exc:  # RuntimeError: resolve()의 심볼릭 링크 루프
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


# ponytail: 수집은 한 번에 하나만 돕니다. 잔여 총량 계산·수집·정리를 한 lock 으로 묶어 동시 수집이
# 같은 잔여량을 두 번 쓰지 않게 합니다. 대상 전송은 여전히 병렬입니다. 수집이 병목이 되면 job 마다
# 바이트를 예약하는 방식으로 바꿉니다.
_collect_lock = threading.Lock()


def collect(app: Flask, job: dict) -> None:
    """원본을 연결 하나로 job 스냅샷 폴더에 받고 manifest 를 확정합니다. 실패하면 차단."""
    with _collect_lock:
        _collect(app, job)


def _collect(app: Flask, job: dict) -> None:
    db, root = app.config["DB_PATH"], app.config["SNAPSHOT_DIR"]
    snap = root / str(job["id"])
    shutil.rmtree(snap, ignore_errors=True)
    recipes = sorted({(idw, idp) for _, idw, idp in job["pairs"]})
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
            # 수집 전후 목록이 같아야 스냅샷을 씁니다. 탐지이지 증명이 아닙니다(스펙 "스냅샷 수집"):
            # 두 목록 사이에 크기·mtime이 같게 바뀐 파일(LIST mtime은 분 단위일 수 있음)은 못 잡습니다.
            for _ in range(2):
                shutil.rmtree(snap, ignore_errors=True)
                before = _listing(ftp, job["class_name"], recipes)
                budget, manifest = left, []
                for files in before.values():
                    for path, _, _ in files:
                        try:
                            data = ftp.download(path, max_bytes=max(budget, 0))
                        except TransferTooLarge:
                            raise _Blocked(f"수집 오류: 스냅샷 총량 상한 {cap} B를 넘습니다.") from None
                        budget -= len(data)
                        _save(app, snap, path, data)
                        manifest.append((path, len(data), hashlib.sha256(data).hexdigest()))
                try:
                    after = _listing(ftp, job["class_name"], recipes)
                except _MissingIdp:  # 편집기가 .idp를 교체하는 중이면 한 번 더 수집합니다.
                    after = None
                if after == before:
                    break
            else:
                raise _Blocked("원본이 바뀌는 중")
        recipe_paths = {r: [path for path, _, _ in files] for r, files in before.items()}
        store.finish_collection(
            db, job["id"], manifest=manifest, manifest_hash=manifest_hash(manifest),
            snapshot_dir=snap, pairs=job["pairs"], recipe_paths=recipe_paths,
        )
    except Exception as exc:  # 어떤 오류든 job 이 '수집 중'에 머물지 않고 사유와 함께 끝나야 합니다.
        app.logger.warning("collection of job %s blocked", job["id"], exc_info=True)
        shutil.rmtree(snap, ignore_errors=True)
        reason = str(exc) if isinstance(exc, _Blocked) else f"수집 오류: {type(exc).__name__}: {exc}"
        _block(db, job["id"], reason)


class Scheduler:
    """시작 판정과 슬롯 예약을 하는 한 곳. 메모리 상태는 이 프로세스에서 시작한 job 뿐입니다.

    대기 job 은 판정 때마다 SQLite 에서 id 순으로 다시 읽습니다. 재기동 전에 시작한 job
    (`collecting`·전송 중으로 남은 것)은 여기 없어 장비도 슬롯도 쥐지 않습니다.
    ponytail: 이어 돌리는 것과 그 job 의 상한 재검사(`pending` -> `failed`)는 티켓 12(재기동 대조 복구).
    """

    def __init__(self, app: Flask) -> None:
        self.app = app
        self.lock = threading.Lock()
        self.running: dict[int, scheduler.Entry] = {}

    def admit(self) -> list[dict]:
        """시작할 수 있는 job 을 모두 수집 중으로 잡고 슬롯을 예약해 돌려줍니다."""
        db, cap = self.app.config["DB_PATH"], self.app.config["MAX_THREADS"]
        with self.lock:
            waiting = {
                j["id"]: scheduler.Entry(j["id"], frozenset([j["source_eqp"], *j["targets"]]), len(j["targets"]), False)
                for j in store.waiting_jobs(db)
            }
            queue = sorted([*self.running.values(), *waiting.values()])
            start, over = scheduler.admit(queue, cap)
            for job_id in over:
                store.block_collection(db, job_id, "waiting", f"상한 초과: 대상 {waiting[job_id].n}대 > 스레드 상한 {cap}")
            claimed = []
            for job_id in start:
                try:
                    claimed.append(store.claim_job(db, job_id))
                except Exception:  # 트랜잭션이 되돌려져 이 job 과 뒤 job 은 대기로 남고 다음 판정에서 다시 봅니다.
                    self.app.logger.exception("claim of job %s failed", job_id)
                    break
                self.running[job_id] = waiting[job_id]._replace(started=True)
            return claimed

    def spawn(self) -> None:
        """운영: 시작 판정한 job 마다 스레드를 띄웁니다. 못 띄운 job 은 사유와 함께 차단하고 예약을 풉니다."""
        for job in self.admit():
            try:
                threading.Thread(target=self._run_logged, args=(job,), name=f"brc-job-{job['id']}", daemon=True).start()
            except Exception:
                self.app.logger.exception("job %s: thread start failed", job["id"])
                try:
                    _block(self.app.config["DB_PATH"], job["id"], "작업자 시작 실패")
                except Exception:
                    # 차단 기록마저 실패하면 job 은 '수집 중'으로 남고 재기동 대조(티켓 12)가 거둡니다.
                    # 뒤 job 들의 인계는 계속합니다.
                    self.app.logger.exception("job %s: could not record the start failure", job["id"])
                finally:
                    with self.lock:
                        del self.running[job["id"]]

    def _run_logged(self, job: dict) -> None:
        try:
            self.run(job, parallel=True)
        except Exception:
            self.app.logger.exception("job %s failed", job["id"])

    def run(self, job: dict, *, parallel: bool) -> None:
        """수집(슬롯 하나) 뒤 대상 작업자들. 마지막 작업자가 끝나면 예약을 풉니다."""
        try:
            collect(self.app, job)
            transfer_job(self.app, job["id"], parallel=parallel)
        finally:
            with self.lock:
                del self.running[job["id"]]
            if wake := self.app.config.get("SCHEDULER_WAKE"):
                wake.set()


def run_until_idle(app: Flask, *, parallel: bool = False) -> None:
    """테스트용: 시작할 수 있는 job 을 판정하고 하나씩 끝까지 돌리기를 더 시작할 job 이 없을 때까지."""
    sched = app.config["SCHEDULER"]
    while claimed := sched.admit():
        for job in claimed:
            sched.run(job, parallel=parallel)


def start_scheduler(app: Flask) -> None:
    """스케줄러 스레드. 시작한 job 은 저마다 스레드에서 수집하고 대상마다 작업자를 띄웁니다."""
    sched, wake = app.config["SCHEDULER"], threading.Event()
    app.config["SCHEDULER_WAKE"] = wake

    def loop():
        while True:
            try:
                sched.spawn()
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


def selection(body, app: Flask) -> dict:
    """사전 점검과 job 생성이 같이 받는 선택(원본, class, 레시피, 대상)을 검사합니다.

    돌려주는 `tools` 는 eqp_id -> 명부의 Tool 입니다.
    """
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
        tools = {t.eqp_id: t for t in hitachi_tools(app.config["ROSTER"]())}
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
    return {"source_eqp": source, "class_name": class_name, "recipes": recipes, "targets": targets, "tools": tools}


def _triples(value, what: str) -> list[tuple[str, str, str]]:
    if not isinstance(value, list) or not all(isinstance(v, dict) for v in value):
        raise _Bad("bad_request", f"{what}는 {{eqp, idw, idp}} 목록이어야 합니다.")
    out = [(v.get("eqp"), v.get("idw"), v.get("idp")) for v in value]
    if not all(isinstance(x, str) for t in out for x in t):
        raise _Bad("bad_request", f"{what}의 eqp, idw, idp 는 문자열이어야 합니다.")
    return list(dict.fromkeys(out))


def _parse(body, app: Flask) -> dict:
    sel = selection(body, app)
    # 확인 팝업의 선택을 그대로 기록합니다. 사전 점검은 다시 돌리지 않습니다(스펙 "사전 점검과 확인").
    every = [(eqp, idw, idp) for eqp in sel["targets"] for idw, idp in sel["recipes"]]
    pairs = every if body.get("pairs") is None else _triples(body["pairs"], "pairs")
    if outside := set(pairs) - set(every):
        raise _Bad("pair_outside_selection", f"대상 × 레시피 밖의 쌍입니다: {'/'.join(min(outside))}")
    if not pairs:
        raise _Bad("no_pairs", "남은 (대상, 레시피) 쌍이 없어 job 을 만들지 않습니다.")
    overwrite = _triples(body.get("overwrite", []), "overwrite")
    if outside := set(overwrite) - set(pairs):
        raise _Bad("overwrite_outside_selection", f"job 에 없는 쌍의 덮어쓰기 허락입니다: {'/'.join(min(outside))}")
    create_class = list(dict.fromkeys(_strings(body.get("create_class", []), "create_class")))
    if outside := set(create_class) - {eqp for eqp, _, _ in pairs}:
        raise _Bad("create_class_outside_selection", f"job 에 없는 대상의 폴더 허락입니다: {', '.join(sorted(outside))}")

    # 새 job 의 크기는 수집 전이라 모릅니다. 수집 중에도 남은 총량으로 다시 막습니다(collect).
    used, limit = snapshot_bytes(app.config["SNAPSHOT_DIR"]), app.config["SNAPSHOT_CAP_BYTES"]
    if used >= limit:
        raise _Bad("snapshot_full", f"스냅샷 총량 {used} B가 상한 {limit} B에 닿아 새 job 을 만들 수 없습니다.", 507)
    return {
        "source_eqp": sel["source_eqp"], "class_name": sel["class_name"], "pairs": pairs,
        "overwrites": overwrite, "create_class": create_class,
    }


def register_jobs(app: Flask) -> None:
    from . import error_json

    app.config["SCHEDULER"] = Scheduler(app)

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
        jobs = store.list_jobs(app.config["DB_PATH"])
        # "대기 n번째" = 앞선 미시작 job 수 + 1. 미시작은 수집 대기뿐입니다.
        waiting = sorted(j["id"] for j in jobs if j["collection"]["state"] == "waiting")
        for j in jobs:
            j["queue_position"] = waiting.index(j["id"]) + 1 if j["id"] in waiting else None
        return {"jobs": jobs}

    @app.get("/api/jobs/<int:job_id>")
    def get_job(job_id):
        job = store.get_job(app.config["DB_PATH"], job_id)
        if job is None:
            return error_json("not_found", f"job {job_id} 이 없습니다.", 404)
        return job
