"""uWSGI 진입점(`application`)과 집 개발 실행(`python index.py`).

`BRC_DEV=1` 이면 가짜 명부와 개발용 신원(`local-dev`, 허용됨)으로 뜹니다.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

from backend import create_app
from backend.roster import fake_roster, office_roster
from ftp_handler.core.client import FtpClient

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

DEV = os.environ.get("BRC_DEV") == "1"
DEV_USER = "local-dev"
VAR = Path(os.environ.get("BRC_VAR_DIR", ROOT / "var"))
allowed = {u.strip() for u in os.environ.get("BRC_ALLOWED_USERS", "").split(",") if u.strip()}


def connect(tool):
    return FtpClient(
        host=tool.eqp_ip,
        user=os.environ.get("BRC_FTP_USER", ""),
        password=os.environ.get("BRC_FTP_PASSWORD", ""),
        encoding=os.environ.get("BRC_FTP_ENCODING", "utf-8"),
    )


application = create_app(
    roster=fake_roster if DEV else office_roster,
    connect=connect,
    db_path=VAR / "brc.sqlite3",
    snapshot_dir=VAR / "snapshots",
    allowed_users=allowed | {DEV_USER} if DEV else allowed,
    dev_user=DEV_USER if DEV else None,
)

if __name__ == "__main__":
    application.run(host="127.0.0.1", port=int(os.environ.get("PORT", 5050)), debug=DEV)
