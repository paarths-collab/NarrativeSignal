# tests/refresh/test_publish_script.py
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "publish_to_data_live.sh"


def _bash_executable() -> str:
    """Resolve to Git Bash explicitly.

    On Windows a bare "bash" can resolve (via PATH search order) to the WSL
    launcher at C:\\Windows\\System32\\bash.exe instead of Git Bash. That
    launcher hands the command line to a Linux distro, which mangles
    Windows-style backslash paths (they get silently eaten as shell escape
    characters) and can't see C:\\... paths at all. Pin to Git Bash so the
    script actually receives the paths we pass it.
    """
    for candidate in (
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
    ):
        if Path(candidate).exists():
            return candidate
    found = shutil.which("bash.exe") or shutil.which("bash")
    if found:
        return found
    raise RuntimeError("no bash executable found")


BASH = _bash_executable()


def _run(cmd, cwd, env=None):
    return subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)


@pytest.fixture()
def bare_remote(tmp_path):
    """A bare repo that acts as `origin` for our scratch push."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    return remote


def test_publish_creates_orphan_branch(scratch_repo, bare_remote, good_artifacts):
    subprocess.run(["git", "-C", str(scratch_repo), "remote", "add", "origin",
                    str(bare_remote)], check=True)

    r = _run([BASH, str(SCRIPT), str(good_artifacts), "test-live"], cwd=scratch_repo)
    assert r.returncode == 0, r.stderr

    # `test-live` on the remote exists and has one commit
    listing = subprocess.run(
        ["git", "-C", str(bare_remote), "log", "--oneline", "test-live"],
        capture_output=True, text=True, check=True,
    ).stdout.strip().splitlines()
    assert len(listing) == 1, "orphan branch must always have exactly one commit"


def test_republishing_replaces_history(scratch_repo, bare_remote, good_artifacts):
    subprocess.run(["git", "-C", str(scratch_repo), "remote", "add", "origin",
                    str(bare_remote)], check=True)

    for _ in range(3):
        assert _run([BASH, str(SCRIPT), str(good_artifacts), "test-live"],
                    cwd=scratch_repo).returncode == 0

    log = subprocess.run(
        ["git", "-C", str(bare_remote), "log", "--oneline", "test-live"],
        capture_output=True, text=True, check=True,
    ).stdout.strip().splitlines()
    assert len(log) == 1, f"expected 1 commit after 3 publishes; got {len(log)}"


def test_publish_refuses_nonexistent_dir(scratch_repo, bare_remote, tmp_path):
    subprocess.run(["git", "-C", str(scratch_repo), "remote", "add", "origin",
                    str(bare_remote)], check=True)
    r = _run([BASH, str(SCRIPT), str(tmp_path / "nope"), "test-live"], cwd=scratch_repo)
    assert r.returncode != 0
