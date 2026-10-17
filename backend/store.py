"""job 저장소. SQL은 전부 여기에 있습니다(스펙 "상태 모델").

상태는 `files.state` 에만 있고 target·recipe·job 상태는 읽을 때 집계합니다. 예외 하나는
file 행이 생기기 전 단계를 나타내는 `jobs.collection` 입니다.
"""

import sqlite3
from collections import Counter
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,  -- FIFO 순서이기도 합니다
  executor TEXT NOT NULL,
  created_at TEXT NOT NULL,
  source_eqp TEXT NOT NULL,
  class_name TEXT NOT NULL,
  include_idw INTEGER NOT NULL DEFAULT 0,
  collection TEXT NOT NULL DEFAULT 'waiting'
    CHECK (collection IN ('waiting', 'collecting', 'done', 'blocked')),
  collection_reason TEXT,
  manifest_hash TEXT,
  snapshot_dir TEXT,
  cancel_requested INTEGER NOT NULL DEFAULT 0,
  closed INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS job_targets (
  job_id INTEGER NOT NULL REFERENCES jobs(id),
  eqp TEXT NOT NULL,
  may_create_class INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (job_id, eqp)
);
-- 사용자가 고른 (대상, 레시피) 쌍. 확인 단계에서 쌍을 뺄 수 있어 대상 × 레시피와 다를 수 있습니다.
CREATE TABLE IF NOT EXISTS job_pairs (
  job_id INTEGER NOT NULL REFERENCES jobs(id),
  eqp TEXT NOT NULL,
  idw TEXT NOT NULL,
  idp TEXT NOT NULL,
  PRIMARY KEY (job_id, eqp, idw, idp)
);
CREATE TABLE IF NOT EXISTS job_overwrites (
  job_id INTEGER NOT NULL REFERENCES jobs(id),
  eqp TEXT NOT NULL,
  idw TEXT NOT NULL,
  idp TEXT NOT NULL,
  PRIMARY KEY (job_id, eqp, idw, idp)
);
CREATE TABLE IF NOT EXISTS manifest (
  job_id INTEGER NOT NULL REFERENCES jobs(id),
  path TEXT NOT NULL,  -- 원격 절대 경로. 대상 경로도 같습니다
  size INTEGER NOT NULL,
  sha256 TEXT NOT NULL,
  PRIMARY KEY (job_id, path)
);
CREATE TABLE IF NOT EXISTS files (
  job_id INTEGER NOT NULL REFERENCES jobs(id),
  target_eqp TEXT NOT NULL,
  path TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'pending'
    CHECK (state IN ('pending', 'in_progress', 'verified', 'failed', 'uncertain')),
  reason TEXT,
  temp_path TEXT,
  PRIMARY KEY (job_id, target_eqp, path)
);
"""

FILE_STATES = ("pending", "in_progress", "verified", "failed", "uncertain")

# 수집 결과의 전이는 이 표 하나로만 일어납니다. done·blocked 는 종료입니다.
_COLLECTION_NEXT = {"waiting": {"collecting", "blocked"}, "collecting": {"done", "blocked"}}
# file 상태 전이도 표 하나로만. verified·failed·uncertain 은 종료입니다.
_FILE_NEXT = {"pending": {"in_progress", "failed"}, "in_progress": {"verified", "failed", "uncertain"}}


@contextmanager
def connect(db_path: Path):
    conn = sqlite3.connect(db_path, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def _tx(conn):
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def init(db_path: Path) -> None:
    with connect(db_path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(_SCHEMA)


def create_job(db_path, *, executor, source_eqp, class_name, pairs) -> int:
    """`pairs` 는 (대상 eqp, idw, idp) 목록입니다."""
    with connect(db_path) as conn, _tx(conn):
        job_id = conn.execute(
            "INSERT INTO jobs (executor, created_at, source_eqp, class_name) VALUES (?, ?, ?, ?)",
            (executor, datetime.now().astimezone().isoformat(timespec="seconds"), source_eqp, class_name),
        ).lastrowid
        conn.executemany(
            "INSERT INTO job_targets (job_id, eqp) VALUES (?, ?)",
            [(job_id, e) for e in dict.fromkeys(e for e, _, _ in pairs)],
        )
        conn.executemany(
            "INSERT INTO job_pairs (job_id, eqp, idw, idp) VALUES (?, ?, ?, ?)",
            [(job_id, *pair) for pair in pairs],
        )
    return job_id


def _move(conn, job_id: int, frm: str, to: str, **fields) -> None:
    if to not in _COLLECTION_NEXT.get(frm, ()):
        raise ValueError(f"수집 결과 전이 불가: {frm} -> {to}")
    sets = ", ".join(["collection = ?", *(f"{k} = ?" for k in fields)])
    cur = conn.execute(
        f"UPDATE jobs SET {sets} WHERE id = ? AND collection = ?", (to, *fields.values(), job_id, frm)
    )
    if cur.rowcount != 1:
        raise RuntimeError(f"job {job_id} 은(는) 이미 {frm} 이 아닙니다")


def claim_next_waiting(db_path):
    """가장 앞선 대기 job 을 수집 중으로 바꾸고 돌려줍니다. 없으면 None.

    `collecting` 으로 남은 job(재기동 전 수집 중이던 것)은 고르지 않습니다. 다시 수집할지는
    티켓 12(재기동 대조 복구)가 정합니다.
    """
    with connect(db_path) as conn, _tx(conn):
        row = conn.execute("SELECT * FROM jobs WHERE collection = 'waiting' ORDER BY id LIMIT 1").fetchone()
        if row is None:
            return None
        _move(conn, row["id"], "waiting", "collecting")
        pairs = [tuple(r) for r in conn.execute(
            "SELECT eqp, idw, idp FROM job_pairs WHERE job_id = ? ORDER BY eqp, idw, idp", (row["id"],))]
    return dict(row) | {"pairs": pairs}


def finish_collection(db_path, job_id, *, manifest, manifest_hash, snapshot_dir, pairs, recipe_paths) -> None:
    """manifest·file 행(쌍마다 대상 × 그 레시피의 파일, pending)·수집 완료를 한 트랜잭션으로 기록합니다.

    `recipe_paths` 는 (idw, idp) -> 그 레시피의 원격 경로 목록입니다.
    """
    with connect(db_path) as conn, _tx(conn):
        conn.executemany(
            "INSERT INTO manifest (job_id, path, size, sha256) VALUES (?, ?, ?, ?)",
            [(job_id, p, size, sha) for p, size, sha in manifest],
        )
        conn.executemany(
            "INSERT INTO files (job_id, target_eqp, path) VALUES (?, ?, ?)",
            [(job_id, eqp, p) for eqp, idw, idp in pairs for p in recipe_paths[(idw, idp)]],
        )
        _move(conn, job_id, "collecting", "done", manifest_hash=manifest_hash, snapshot_dir=str(snapshot_dir))


def block_collection(db_path, job_id, frm: str, reason: str) -> None:
    with connect(db_path) as conn, _tx(conn):
        _move(conn, job_id, frm, "blocked", collection_reason=reason)


def transfer_plan(db_path, job_id: int) -> dict | None:
    """수집이 끝난 job 의 전송 재료. 수집이 `done` 이 아니면 None."""
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None or row["collection"] != "done":
            return None
        pending: dict[str, list[str]] = {}
        for r in conn.execute(
            "SELECT target_eqp, path FROM files WHERE job_id = ? AND state = 'pending' ORDER BY target_eqp, path",
            (job_id,),
        ):
            pending.setdefault(r["target_eqp"], []).append(r["path"])
        return {
            "id": job_id,
            "class_name": row["class_name"],
            "snapshot_dir": row["snapshot_dir"],
            "manifest": {r["path"]: r["sha256"] for r in conn.execute(
                "SELECT path, sha256 FROM manifest WHERE job_id = ?", (job_id,))},
            "pairs": [tuple(r) for r in conn.execute(
                "SELECT eqp, idw, idp FROM job_pairs WHERE job_id = ? ORDER BY eqp, idw, idp", (job_id,))],
            "overwrites": {tuple(r) for r in conn.execute(
                "SELECT eqp, idw, idp FROM job_overwrites WHERE job_id = ?", (job_id,))},
            "pending": pending,
        }


def move_file(db_path, job_id, eqp, path, frm: str, to: str, *, reason=None, temp_path=None) -> bool:
    """file 하나를 `frm` -> `to` 로. 이미 `frm` 이 아니면(다른 작업자가 실패 처리 등) False."""
    if to not in _FILE_NEXT.get(frm, ()):
        raise ValueError(f"file 상태 전이 불가: {frm} -> {to}")
    with connect(db_path) as conn:
        cur = conn.execute(
            "UPDATE files SET state = ?, reason = ?, temp_path = ? "
            "WHERE job_id = ? AND target_eqp = ? AND path = ? AND state = ?",
            (to, reason, temp_path, job_id, eqp, path, frm),
        )
    return cur.rowcount == 1


def fail_pending(db_path, job_id, reason: str, *, eqp=None, paths=None) -> None:
    """남은 `pending` 을 `failed(reason)` 로. `eqp`·`paths` 로 범위를 좁힙니다."""
    sql, args = "UPDATE files SET state = 'failed', reason = ? WHERE job_id = ? AND state = 'pending'", [reason, job_id]
    if eqp is not None:
        sql += " AND target_eqp = ?"
        args.append(eqp)
    with connect(db_path) as conn, _tx(conn):
        if paths is None:
            conn.execute(sql, args)
        else:
            conn.executemany(sql + " AND path = ?", [(*args, p) for p in paths])


def _summary(conn, row) -> dict:
    job_id = row["id"]
    counts = Counter({s: 0 for s in FILE_STATES})
    counts.update({r["state"]: r["n"] for r in conn.execute(
        "SELECT state, count(*) AS n FROM files WHERE job_id = ? GROUP BY state", (job_id,))})
    m = conn.execute("SELECT count(*) AS n, coalesce(sum(size), 0) AS b FROM manifest WHERE job_id = ?",
                     (job_id,)).fetchone()
    # 로컬 스냅샷 경로는 조회 응답에 넣지 않습니다(스펙 "권한").
    return {
        "id": job_id,
        "executor": row["executor"],
        "created_at": row["created_at"],
        "source_eqp": row["source_eqp"],
        "class_name": row["class_name"],
        "include_idw": bool(row["include_idw"]),
        "collection": {"state": row["collection"], "reason": row["collection_reason"]},
        "manifest": {"file_count": m["n"], "total_bytes": m["b"]},
        "target_count": conn.execute("SELECT count(*) FROM job_targets WHERE job_id = ?", (job_id,)).fetchone()[0],
        "recipe_count": conn.execute(
            "SELECT count(*) FROM (SELECT DISTINCT idw, idp FROM job_pairs WHERE job_id = ?)", (job_id,)).fetchone()[0],
        "file_counts": dict(counts),
        "cancel_requested": bool(row["cancel_requested"]),
        "closed": bool(row["closed"]),
    }


def list_jobs(db_path) -> list[dict]:
    with connect(db_path) as conn:
        return [_summary(conn, r) for r in conn.execute("SELECT * FROM jobs ORDER BY id DESC")]


def get_job(db_path, job_id: int) -> dict | None:
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            return None
        return _summary(conn, row) | {
            "targets": [{"eqp": r["eqp"], "may_create_class": bool(r["may_create_class"])} for r in conn.execute(
                "SELECT eqp, may_create_class FROM job_targets WHERE job_id = ? ORDER BY eqp", (job_id,))],
            "recipes": [{"idw": r["idw"], "idp": r["idp"]} for r in conn.execute(
                "SELECT DISTINCT idw, idp FROM job_pairs WHERE job_id = ? ORDER BY idw, idp", (job_id,))],
            "pairs": [{"eqp": r["eqp"], "idw": r["idw"], "idp": r["idp"]} for r in conn.execute(
                "SELECT eqp, idw, idp FROM job_pairs WHERE job_id = ? ORDER BY eqp, idw, idp", (job_id,))],
            # 결과 트리(티켓 08) 전까지 실패·불명확 file 만 평평하게 보여 줍니다.
            "problems": [dict(r) for r in conn.execute(
                "SELECT target_eqp AS eqp, path, state, reason, temp_path FROM files "
                "WHERE job_id = ? AND state IN ('failed', 'uncertain') ORDER BY target_eqp, path", (job_id,))],
        }
