import os
import threading
from ftplib import error_perm

import pytest
from pyftpdlib.authorizers import DummyAuthorizer
from pyftpdlib.handlers import FTPHandler
from pyftpdlib.servers import FTPServer

from ftp_handler.core import FtpClient, TargetExists, TransferUncertain


class DropOnRenameHandler(FTPHandler):
    """Runs the rename, then hangs up before replying: a lost RNTO reply."""

    def ftp_RNTO(self, path):
        os.rename(self._rnfr, path)
        self.close()


class NoClobberRenameHandler(FTPHandler):
    """A server whose RNTO refuses an existing target (OFFICE-VERIFY 3)."""

    def ftp_RNTO(self, path):
        if os.path.exists(path):
            self._rnfr = None
            self.respond("550 Target exists.")
            return
        super().ftp_RNTO(path)


class _Server:
    """A real pyftpdlib server on a free 127.0.0.1 port, rooted at ``root``."""

    def __init__(self, root, handler_base=FTPHandler):
        authorizer = DummyAuthorizer()
        authorizer.add_user("u", "p", str(root), perm="elradfmw")
        handler = type("H", (handler_base,), {"authorizer": authorizer})
        self._server = FTPServer(("127.0.0.1", 0), handler)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        while not self._stop.is_set():
            self._server.serve_forever(timeout=0.05, blocking=False)
        self._server.close_all()

    def client(self):
        return FtpClient(host="127.0.0.1", port=self._server.address[1], user="u", password="p")

    def close(self):
        self._stop.set()
        self._thread.join()


@pytest.fixture
def make_server(tmp_path):
    servers = []

    def make(handler_base=FTPHandler):
        servers.append(_Server(tmp_path, handler_base))
        return servers[-1]

    yield make
    for server in servers:
        server.close()


@pytest.fixture
def ftp(make_server):
    with make_server().client() as c:
        yield c


def _files(root):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


def test_put_writes_new_file(ftp, tmp_path):
    ftp.put("/a.idp", b"new")
    assert (tmp_path / "a.idp").read_bytes() == b"new"
    assert _files(tmp_path) == ["a.idp"]


def test_put_refuses_existing_target(ftp, tmp_path):
    (tmp_path / "a.idp").write_bytes(b"old")
    with pytest.raises(TargetExists):
        ftp.put("/a.idp", b"new")
    assert (tmp_path / "a.idp").read_bytes() == b"old"
    assert _files(tmp_path) == ["a.idp"]


def test_put_overwrite_replaces_existing_file(ftp, tmp_path):
    (tmp_path / "a.idp").write_bytes(b"old")
    ftp.put("/a.idp", b"new", overwrite=True)
    assert (tmp_path / "a.idp").read_bytes() == b"new"
    assert _files(tmp_path) == ["a.idp"]


def test_put_overwrite_on_server_whose_rnto_refuses_existing(make_server, tmp_path):
    (tmp_path / "a.idp").write_bytes(b"old")
    with make_server(NoClobberRenameHandler).client() as c:
        c.put("/a.idp", b"new", overwrite=True)
    assert (tmp_path / "a.idp").read_bytes() == b"new"
    assert _files(tmp_path) == ["a.idp"]


def test_put_overwrite_without_existing_target(ftp, tmp_path):
    ftp.put("/a.idp", b"new", overwrite=True)
    assert (tmp_path / "a.idp").read_bytes() == b"new"


def test_put_overwrite_lost_rename_reply_is_uncertain(make_server, tmp_path):
    (tmp_path / "a.idp").write_bytes(b"old")
    with make_server(DropOnRenameHandler).client() as c:
        with pytest.raises(TransferUncertain) as info:
            c.put("/a.idp", b"new", overwrite=True)
    assert info.value.temp_path.startswith("/a.idp.") and info.value.temp_path.endswith(".part")
    assert (tmp_path / "a.idp").read_bytes() == b"new"


def test_mkdir_creates_nested_path(ftp, tmp_path):
    ftp.mkdir("/CLS/data/W1")
    assert (tmp_path / "CLS" / "data" / "W1").is_dir()


def test_mkdir_existing_dir_is_success(ftp, tmp_path):
    (tmp_path / "CLS" / "data").mkdir(parents=True)
    ftp.mkdir("/CLS/data")
    ftp.mkdir("/CLS/data/W1")
    assert _files(tmp_path) == ["CLS", "CLS/data", "CLS/data/W1"]
    assert ftp.home() == "/"


def test_mkdir_never_creates_the_floor_or_above(ftp, tmp_path):
    (tmp_path / "CLS" / "data").mkdir(parents=True)
    ftp.mkdir("/CLS/data/W1/P1", floor="/CLS/data")
    assert _files(tmp_path) == ["CLS", "CLS/data", "CLS/data/W1", "CLS/data/W1/P1"]
    (tmp_path / "CLS" / "data" / "W1" / "P1").rmdir()
    (tmp_path / "CLS" / "data" / "W1").rmdir()
    (tmp_path / "CLS" / "data").rmdir()
    with pytest.raises(error_perm, match="^550"):
        ftp.mkdir("/CLS/data/W1", floor="/CLS/data")
    assert _files(tmp_path) == ["CLS"]


def test_mkdir_over_existing_file_raises(ftp, tmp_path):
    (tmp_path / "CLS").write_bytes(b"x")
    with pytest.raises(error_perm, match="^550"):
        ftp.mkdir("/CLS/data")
