"""Boot-path behaviour of backend/entrypoint.sh.

The entrypoint runs on every container start. We verify:
  1. When FETCH_DATA_LIVE_URL is set and the download fails, the fetch
     helper falls back to seed and the entrypoint still runs patch_db.py.
  2. SKIP_UVICORN=1 is honoured (the test hook that lets us exercise the
     pre-server boot path without actually starting uvicorn).
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
ENTRY = REPO_ROOT / "backend" / "entrypoint.sh"

# On Windows, `bash` on PATH may resolve to the WSL launcher stub in
# C:\Windows\System32\bash.exe, which mangles backslash paths passed as
# arguments. shutil.which honours PATH order so it finds git-bash first
# when git-bash was installed after enabling "add to PATH".
BASH = shutil.which("bash") or "bash"


@pytest.fixture()
def fake_env(tmp_path, good_artifacts):
    """A fake /app layout with a seed and a patch_db.py that just touches a file."""
    app = tmp_path / "app"
    (app / "seed_data").mkdir(parents=True)
    (app / "data").mkdir()
    (app / "backend").mkdir()
    # copy good_artifacts into seed
    for f in good_artifacts.iterdir():
        shutil.copy2(f, app / "seed_data" / f.name)
    # stub patch_db.py: it just writes a marker
    (app / "backend" / "patch_db.py").write_text(
        "import os, pathlib; "
        "pathlib.Path(os.environ['DATA_PATH'], 'patched').write_text('ok')\n"
    )
    # copy the real entrypoint into the fake tree so cwd resolves correctly
    (app / "backend" / "entrypoint.sh").write_text(ENTRY.read_text())
    (app / "backend" / "entrypoint.sh").chmod(0o755)
    return app


def _run_entry(app, extra_env=None):
    env = {
        **os.environ,
        "DATA_PATH": str(app / "data"),
        "SEED_PATH": str(app / "seed_data"),
        "FETCH_DATA_LIVE_URL": "http://127.0.0.1:1/nope.tar.gz",  # forces fallback
        "FETCH_DATA_LIVE_SCRIPT": str(REPO_ROOT / "scripts" / "fetch_data_live.sh"),
        "SKIP_UVICORN": "1",  # entrypoint respects this and exits cleanly
        **(extra_env or {}),
    }
    return subprocess.run(
        [BASH, str(app / "backend" / "entrypoint.sh")],
        cwd=app / "backend", env=env, capture_output=True, text=True,
    )


def test_fallback_seeds_data_and_runs_patch_db(fake_env):
    r = _run_entry(fake_env)
    assert r.returncode == 0, r.stderr + r.stdout
    assert (fake_env / "data" / "clean_posts.csv").exists()
    assert (fake_env / "data" / "patched").exists()


def test_skip_uvicorn_is_respected(fake_env):
    """The test hook that lets us verify the pre-uvicorn boot path without a server.

    We can't just grep for the string "uvicorn" in stdout — the pytest tmp_path
    includes the test function name (which contains "uvicorn"), the fetch
    helper echoes `seeded from <SEED_PATH>` to stdout, and the entrypoint's
    own SKIP echo mentions SKIP_UVICORN. Instead we assert the two signals
    that are decisive: the SKIP branch's completion marker MUST be present
    (proving we exited before `exec uvicorn`), and the ASGI import failure
    that uvicorn produces when it runs against our fake tree MUST be absent
    (proving `exec uvicorn` never fired).
    """
    r = _run_entry(fake_env)
    assert r.returncode == 0, r.stderr + r.stdout
    assert "boot verification complete" in r.stdout, \
        "SKIP branch marker missing; entrypoint may have fallen through to uvicorn"
    assert "Uvicorn running" not in r.stdout, "uvicorn banner in stdout"
    assert "Error loading ASGI app" not in r.stderr, \
        "uvicorn was invoked (it errored on the missing main module)"
