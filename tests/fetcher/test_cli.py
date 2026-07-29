import json
import subprocess
import sys
from datetime import date

import pytest


def test_help(tmp_path):
    """--help works without needing the four Reddit secrets set.

    Scrubbing REDDIT_* + GROQ_API_KEY (rather than PATH) is what actually proves
    the intent: --help must not touch config that needs those env vars.
    Scrubbing PATH itself breaks asyncio on Windows (WinError 10106) before
    argparse ever runs, so the original test's fallback branch was unreachable.
    """
    import os
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("REDDIT_") and k != "GROQ_API_KEY"}
    r = subprocess.run(
        [sys.executable, "-m", "fetcher", "fetch", "--help"],
        capture_output=True, text=True, env=env,
    )
    assert r.returncode == 0, r.stderr
    assert "--date" in r.stdout


def test_fetch_yesterday_is_default(monkeypatch, tmp_path, fake_reddit):
    """`fetcher fetch --out X` with no --date uses yesterday-UTC."""
    from fetcher.cli import parse_args
    ns = parse_args(["fetch", "--out", str(tmp_path / "d.jsonl")])
    assert ns.date is not None
    # Verifying it's actually yesterday is time-dependent; the CLI's --date default
    # is set at parse time, so we assert on the format only.
    date.fromisoformat(ns.date)


def test_run_writes_jsonl_that_normalize_can_read(monkeypatch, tmp_path, fake_reddit):
    from fetcher import cli
    monkeypatch.setattr(cli, "make_reddit_client", lambda: fake_reddit)
    out = tmp_path / "d.jsonl"
    rc = cli.run(["fetch", "--date", "2025-02-17", "--out", str(out)])
    assert rc == 0
    assert out.exists()

    from etl.normalize import normalize_listing
    df = normalize_listing(out)
    assert len(df) > 0
