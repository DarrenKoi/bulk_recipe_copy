"""메모리 가짜 장비. 테스트와 집 개발 실행(`BRC_DEV=1`)이 같이 씁니다.

장비 하나는 파일 트리(경로 -> 내용·수정 시각)이고, 앱이 부르는 `FtpClient` 메서드만
흉내 냅니다. 장비에 닿는 연결이 이 객체 자체이므로 `connect`는 `lambda t: fleet[t.eqp_id]`.
"""

from datetime import datetime, timedelta, timezone
from ftplib import error_perm
from pathlib import PurePosixPath

from ftp_handler.core.client import FileInfo, ListingTooLarge, TransferTimeout, TransferTooLarge

from . import layout

KST = timezone(timedelta(hours=9), "KST")


class FakeTool:
    def __init__(self) -> None:
        self.files: dict[str, tuple[bytes, datetime]] = {}
        self.dirs: set[str] = set()  # 빈 폴더만 적으면 됩니다. 파일의 상위 폴더는 저절로 있습니다.
        self.reachable = True
        self.stalled = False  # True 면 LIST가 끝나지 않는 장비처럼 deadline 에 걸립니다.

    def add(self, path: str, data: bytes, mtime: datetime) -> None:
        self.files[path] = (data, mtime)

    def add_recipe(
        self, class_name: str, idw: str, idp: str, raw: dict[str, bytes], mtime: datetime
    ) -> None:
        """`raw`는 raw 폴더 기준 상대 경로 -> 내용. 하위 폴더는 `sub/x.tif`처럼."""
        self.add(layout.idp_path(class_name, idw, idp), f"IDP {idp}\n".encode(), mtime)
        raw_dir = layout.raw_dir(class_name, idw, idp)
        self.dirs.add(raw_dir)
        for rel, data in raw.items():
            self.add(f"{raw_dir}/{rel}", data, mtime)

    def _all_dirs(self) -> set[str]:
        out = set()
        for p in [*self.files, *self.dirs]:
            out.update(str(a) for a in PurePosixPath(p).parents)
        return out | self.dirs

    def __enter__(self) -> "FakeTool":
        if not self.reachable:
            raise ConnectionRefusedError("[Errno 61] Connection refused")
        return self

    def __exit__(self, *exc: object) -> None:
        pass

    def list_details(
        self,
        remote_dir: str,
        pattern: str | None = None,
        *,
        max_entries: int | None = None,
        deadline: float | None = None,
        **_,
    ) -> list[FileInfo]:
        if self.stalled:
            raise TransferTimeout(f"LIST {remote_dir} exceeded {deadline}s")
        remote_dir = remote_dir.rstrip("/") or "/"
        dirs = self._all_dirs()
        if remote_dir not in dirs:
            raise error_perm(f"550 {remote_dir}: No such file or directory")
        out = [
            FileInfo(PurePosixPath(d).name, d, True, None, None, "")
            for d in sorted(dirs)
            if str(PurePosixPath(d).parent) == remote_dir and d != remote_dir
        ]
        for path, (data, mtime) in sorted(self.files.items()):
            p = PurePosixPath(path)
            if str(p.parent) == remote_dir and (pattern is None or p.match(pattern)):
                out.append(FileInfo(p.name, path, False, len(data), mtime, ""))
        if max_entries is not None and len(out) > max_entries:
            raise ListingTooLarge(f"{remote_dir} has over {max_entries} LIST lines")
        return out

    def download(self, remote_path: str, *, max_bytes: int | None = None, **_) -> bytes:
        try:
            data = self.files[remote_path][0]
        except KeyError:
            raise error_perm(f"550 {remote_path}: No such file or directory") from None
        if max_bytes is not None and len(data) > max_bytes:
            raise TransferTooLarge(f"{remote_path} exceeds {max_bytes} bytes")
        return data

    def list_idp_files(self, data_dir: str, **limits) -> list[FileInfo]:
        """`data/*/*.idp`를 한 번에. 실제 장비의 명령은 티켓 13에서 정합니다."""
        return [
            f
            for d in self.list_details(data_dir, **limits)
            if d.is_dir
            for f in self.list_details(d.path, "*.idp", **limits)
            if not f.is_dir
        ]


def dev_fleet() -> dict[str, FakeTool]:
    """집 개발용. 장비 id는 `roster.FAKE_TOOLS`의 Hitachi 장비와 같습니다."""
    now = datetime.now(KST).replace(microsecond=0)
    raw = {
        "image001.tif": b"\x49\x49\x2a\x00" + bytes(2048),
        "image002.tif": b"\x49\x49\x2a\x00" + bytes(4096),
        "meas/point.csv": b"x,y\n1,2\n",
    }
    fleet = {eqp: FakeTool() for eqp in ("6MCDE301", "6MCDE305", "6MCDE402", "4CCD6701", "3RHV0101")}

    src = fleet["6MCDE301"]
    # W_SHARED 는 두 레시피가 같이 쓰는 idw 입니다.
    src.add(layout.idw_path("ABC123_GATE", "W_SHARED"), b"IDW W_SHARED\n", now - timedelta(days=3))
    src.add_recipe("ABC123_GATE", "W_SHARED", "GATE_CD_01", raw, now - timedelta(minutes=20))
    src.add_recipe("ABC123_GATE", "W_SHARED", "GATE_CD_02", raw, now - timedelta(hours=2))
    src.add_recipe("ABC123_GATE", "W_GATE_OLD", "GATE_CD_01", {"a.tif": bytes(512)}, now - timedelta(days=40))
    src.add_recipe("ABC123_M1", "W_M1", "M1_LINE", raw, now - timedelta(days=1))
    src.dirs.add(layout.data_dir("XYZ900_EMPTY"))

    fleet["6MCDE305"].add_recipe("ABC123_GATE", "W_SHARED", "GATE_CD_02", raw, now - timedelta(days=5))
    fleet["6MCDE402"].dirs.add(layout.data_dir("ABC123_GATE"))
    fleet["4CCD6701"].add_recipe("ABC123_M1", "W_M1", "M1_LINE", {"old.tif": bytes(64)}, now - timedelta(days=9))
    fleet["3RHV0101"].reachable = False
    return fleet
