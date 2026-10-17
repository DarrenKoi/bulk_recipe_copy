"""레시피 레이아웃. 레시피 경로를 조립하는 유일한 곳입니다(스펙 "레시피의 정의").

OFFICE-VERIFY 2(`.idw` 위치, raw 폴더 제외 규칙)의 답이 바뀌면 여기만 고칩니다.
"""

from pathlib import PurePosixPath

ROOT = "/HITACHI/DEVICE/HD"


def _part(value: str) -> str:
    if value in ("", ".", "..") or "/" in value or "\\" in value:
        raise ValueError(f"경로 성분으로 쓸 수 없는 값입니다: {value!r}")
    return value


def child(remote_dir: str, name: str) -> str:
    """`remote_dir` 의 직계 자식 경로. 장비가 준 목록 항목 이름도 이것으로 검사합니다."""
    return f"{remote_dir.rstrip('/')}/{_part(name)}"


def class_dir(class_name: str) -> str:
    return f"{ROOT}/{_part(class_name)}"


def data_dir(class_name: str) -> str:
    return f"{class_dir(class_name)}/data"


def idw_dir(class_name: str, idw: str) -> str:
    return f"{data_dir(class_name)}/{_part(idw)}"


def idp_path(class_name: str, idw: str, idp: str) -> str:
    return f"{idw_dir(class_name, idw)}/{_part(idp)}.idp"


def raw_dir(class_name: str, idw: str, idp: str) -> str:
    return f"{idw_dir(class_name, idw)}/{_part(idp)}"


def idw_path(class_name: str, idw: str) -> str:
    # 가정(OFFICE-VERIFY 2): `.idw`는 `data/` 바로 아래에 있습니다.
    return f"{data_dir(class_name)}/{_part(idw)}.idw"


def recipe_of_idp_path(path: str) -> tuple[str, str]:
    """`.../data/{idw}/{idp}.idp` -> (idw, idp)."""
    p = PurePosixPath(path)
    return p.parent.name, p.stem
