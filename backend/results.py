"""결과 집계(스펙 "상태 모델"). 상태는 file 에만 있고, 장비·job 결과는 여기서 file 상태로만 계산합니다."""

from collections import Counter

from . import layout

FILE_STATES = ("pending", "in_progress", "verified", "failed", "uncertain")


def counts(states) -> dict[str, int]:
    c = Counter(dict.fromkeys(FILE_STATES, 0))
    c.update(states)
    return dict(c)


def target_state(c: dict[str, int]) -> str | None:
    """file 이 하나도 없으면(수집 전) None. 남은 file 이 있으면 최종 결과를 정하지 않습니다."""
    if not sum(c.values()):
        return None
    if c["pending"] or c["in_progress"]:
        return "running"
    # 불명확이 먼저입니다. 사람이 장비에서 직접 확인해야 하는 쪽이기 때문입니다.
    if c["uncertain"]:
        return "uncertain"
    return "failed" if c["failed"] else "verified"


def job_progress(collection: str, c: dict[str, int]) -> dict:
    """처리율과 성공을 따로. 수집 전(행 없음)은 처리율이 없고(None) 성공도 아닙니다."""
    total = sum(c.values())
    processed = total - c["pending"] - c["in_progress"]
    finished = collection == "blocked" or (collection == "done" and processed == total)
    success = finished and collection == "done" and total > 0 and c["verified"] == total
    if collection == "waiting":
        status = "waiting"
    elif not finished:
        status = "running"
    else:
        status = "done" if success else "failed"
    return {
        "status": status,
        "finished": finished,
        "success": success,
        "processed": processed,
        "total": total,
        "rate": processed / total if total else None,
    }


def _in_recipe(class_name: str, idw: str, idp: str, path: str) -> bool:
    # 공유 `.idw` 는 그것을 쓰는 레시피 모두에 보입니다(스펙 "장비 작업자").
    return (
        path == layout.idp_path(class_name, idw, idp)
        or path.startswith(layout.raw_dir(class_name, idw, idp) + "/")
        or path == layout.idw_path(class_name, idw)
    )


def tree(class_name: str, targets: list[str], pairs, files: list[dict], target_only: dict) -> list[dict]:
    """장비 → 레시피 → 파일.

    `pairs` 는 (eqp, idw, idp), `files` 는 {eqp, path, state, reason, temp_path},
    `target_only` 는 (eqp, idw, idp) -> 대상에만 있는 파일 수(LIST 하지 못했으면 키가 없음).
    """
    out = []
    for eqp in targets:
        mine = [f for f in files if f["eqp"] == eqp]
        c = counts(f["state"] for f in mine)
        out.append({
            "eqp": eqp,
            "state": target_state(c),
            "counts": c,
            "recipes": [
                {
                    "idw": idw,
                    "idp": idp,
                    "target_only": target_only.get((eqp, idw, idp)),
                    "files": [
                        {k: f[k] for k in ("path", "state", "reason", "temp_path")}
                        for f in mine if _in_recipe(class_name, idw, idp, f["path"])
                    ],
                }
                for e, idw, idp in pairs if e == eqp
            ],
        })
    return out
