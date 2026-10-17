"""장비 명부. 공급자는 `() -> list[Tool]`이고 앱 팩토리에 주입합니다.

운영은 office Redis `v3_df_sem_avail`(접속 확인된 장비, 읽기 전용), 집·테스트는 고정 목록입니다.
계약은 docs/datatables/hitachi/sem_list.txt 입니다.
"""

from dataclasses import dataclass

ROSTER_KEY = "v3_df_sem_avail"
VENDORS = {"HITACHI", "AMAT"}


@dataclass(frozen=True)
class Tool:
    eqp_id: str
    eqp_ip: str
    fab_name: str
    model: str
    vendor: str


def hitachi_tools(tools: list[Tool]) -> list[Tool]:
    return sorted((t for t in tools if t.vendor == "HITACHI"), key=lambda t: t.eqp_id)


def _text(value) -> str:
    """Cell as stripped text; "" for NaN/None, bytes decoded as UTF-8 (not "b'...'")."""
    if value is None or value != value:
        return ""
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8").strip()
    return str(value).strip()


def office_roster() -> list[Tool]:
    from .office_redis import read_parquet_key

    return tools_from_dataframe(read_parquet_key(ROSTER_KEY))


def tools_from_dataframe(df) -> list[Tool]:
    missing = {"eqp_id", "eqp_ip", "fab_name", "eqp_model_cd", "vendor_nm"} - set(df.columns)
    if missing:
        raise LookupError(f"{ROSTER_KEY!r} is missing columns {sorted(missing)}")
    tools = []
    for rec in df.to_dict(orient="records"):
        vendor = _text(rec["vendor_nm"]).upper()
        if vendor not in VENDORS:
            raise LookupError(f"unexpected vendor_nm {rec['vendor_nm']!r} for {rec['eqp_id']!r}")
        tools.append(
            Tool(
                eqp_id=_text(rec["eqp_id"]),
                eqp_ip=_text(rec["eqp_ip"]),
                fab_name=_text(rec["fab_name"]),
                model=_text(rec["eqp_model_cd"]),
                vendor=vendor,
            )
        )
    return tools


# 집 개발용 가짜 명부. IP는 문서용 대역(RFC 5737)입니다.
FAKE_TOOLS = [
    Tool("6MCDE301", "192.0.2.11", "M16A", "CG6300", "HITACHI"),
    Tool("6MCDE305", "192.0.2.12", "M16A", "CG6380", "HITACHI"),
    Tool("6MCDE402", "192.0.2.13", "M16B", "GT2000", "HITACHI"),
    Tool("4CCD6701", "192.0.2.21", "M14", "CG5000", "HITACHI"),
    Tool("3RHV0101", "192.0.2.31", "R3", "TP4000", "HITACHI"),
    Tool("6MVRT001", "192.0.2.41", "M16A", "VERITYSEM10", "AMAT"),
]


def fake_roster() -> list[Tool]:
    return list(FAKE_TOOLS)
