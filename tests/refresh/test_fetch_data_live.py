import http.server
import shutil
import socketserver
import subprocess
import tarfile
import threading
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "fetch_data_live.sh"

# On Windows, invoking the bare "bash" command can resolve to the WSL launcher
# stub in C:\Windows\System32\bash.exe (CreateProcess checks System32 before
# PATH), which mangles Windows-style backslash paths passed as arguments and
# drops extra positional args. Resolve the real (git-bash) interpreter
# explicitly via PATH lookup, which does not have that priority quirk.
BASH = shutil.which("bash") or "bash"


class _FakeServer:
    def __init__(self, root: Path):
        self.root = root
        self.port = 0
        self._srv = None
        self._thread = None

    def __enter__(self):
        handler = lambda *a, **kw: http.server.SimpleHTTPRequestHandler(
            *a, directory=str(self.root), **kw
        )
        self._srv = socketserver.TCPServer(("127.0.0.1", 0), handler)
        self.port = self._srv.server_address[1]
        self._thread = threading.Thread(target=self._srv.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_):
        self._srv.shutdown()
        self._srv.server_close()


@pytest.fixture()
def artifacts_tarball(good_artifacts, tmp_path):
    """Package good_artifacts as GitHub does: repo-name-branch/... prefix."""
    tar_dir = tmp_path / "serve"
    tar_dir.mkdir()
    tar_path = tar_dir / "narrative.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tar:
        tar.add(good_artifacts, arcname="paarths-collab-narrative-abcd/")
    return tar_dir, tar_path.name


def test_fetch_downloads_and_extracts(artifacts_tarball, tmp_path):
    tar_dir, tar_name = artifacts_tarball
    dest = tmp_path / "app-data"
    dest.mkdir()

    with _FakeServer(tar_dir) as srv:
        url = f"http://127.0.0.1:{srv.port}/{tar_name}"
        r = subprocess.run(
            [BASH, str(SCRIPT), url, str(dest), "/nonexistent/seed"],
            capture_output=True, text=True,
        )
    assert r.returncode == 0, r.stderr
    assert (dest / "clean_posts.csv").exists()


def test_fetch_falls_back_to_seed_when_url_fails(tmp_path, good_artifacts):
    dest = tmp_path / "app-data"
    dest.mkdir()

    r = subprocess.run(
        [BASH, str(SCRIPT), "http://127.0.0.1:1/nope.tar.gz",
         str(dest), str(good_artifacts)],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
    assert (dest / "clean_posts.csv").exists()
    assert "fallback" in (r.stdout + r.stderr).lower()


def test_fetch_hard_fails_when_no_seed(tmp_path):
    dest = tmp_path / "app-data"
    dest.mkdir()

    r = subprocess.run(
        [BASH, str(SCRIPT), "http://127.0.0.1:1/nope.tar.gz",
         str(dest), "/nonexistent/seed"],
        capture_output=True, text=True,
    )
    assert r.returncode != 0, "must fail loudly when both download AND seed fail"
