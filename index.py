"""uWSGI 진입점(`application`)과 집 개발 실행(`python index.py`).

`BRC_DEV=1` 이면 가짜 명부·샘플 레시피를 채운 가짜 장비와 개발용 신원(`local-dev`, 허용됨)으로 뜹니다.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

from backend import create_app
from backend.browse import HitachiFtpClient
from backend.fake_fleet import dev_fleet
from backend.roster import fake_roster, office_roster

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

DEV = os.environ.get("BRC_DEV") == "1"
DEV_USER = "local-dev"
VAR = Path(os.environ.get("BRC_VAR_DIR", ROOT / "var"))
allowed = {u.strip() for u in os.environ.get("BRC_ALLOWED_USERS", "").split(",") if u.strip()}


def connect(tool):
    return HitachiFtpClient(
        host=tool.eqp_ip,
        user=os.environ.get("BRC_FTP_USER", ""),
        password=os.environ.get("BRC_FTP_PASSWORD", ""),
        encoding=os.environ.get("BRC_FTP_ENCODING", "utf-8"),
    )


FLEET = dev_fleet() if DEV else {}

application = create_app(
    roster=fake_roster if DEV else office_roster,
    connect=(lambda tool: FLEET[tool.eqp_id]) if DEV else connect,
    db_path=VAR / "brc.sqlite3",
    snapshot_dir=VAR / "snapshots",
    allowed_users=allowed | {DEV_USER} if DEV else allowed,
    dev_user=DEV_USER if DEV else None,
    browse_seconds=float(os.environ.get("BRC_BROWSE_SECONDS", 5)),
    browse_max_entries=int(os.environ.get("BRC_BROWSE_MAX_ENTRIES", 5000)),
    max_threads=int(os.environ.get("BRC_MAX_THREADS", 32)),
    snapshot_cap_bytes=int(float(os.environ.get("BRC_SNAPSHOT_CAP_GB", 20)) * 1024**3),
    transfer_seconds=float(os.environ.get("BRC_TRANSFER_SECONDS", 60)),
    transfer_max_bytes=int(float(os.environ.get("BRC_TRANSFER_MAX_MB", 64)) * 1024**2),
    transfer_retries=int(os.environ.get("BRC_TRANSFER_RETRIES", 3)),
)

if __name__ == "__main__":
    application.run(host="127.0.0.1", port=int(os.environ.get("PORT", 5050)), debug=DEV)
