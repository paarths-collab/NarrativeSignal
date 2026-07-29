import subprocess
import sys
from pathlib import Path

import pytest

from scripts.validate_artifacts import ValidationError, validate

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_good_artifacts_pass(good_artifacts):
    validate(good_artifacts)  # must not raise


def test_missing_file_fails(good_artifacts):
    (good_artifacts / "clean_posts.csv").unlink()
    with pytest.raises(ValidationError, match="missing"):
        validate(good_artifacts)


def test_empty_file_fails(good_artifacts):
    (good_artifacts / "narrative_registry.csv").write_text("")
    with pytest.raises(ValidationError, match="empty"):
        validate(good_artifacts)


def test_zero_row_file_fails(good_artifacts):
    """Header alone is not enough — every CSV must have at least one data row."""
    (good_artifacts / "daily_volume_v2.csv").write_text(
        "created_datetime,post_count\n", encoding="utf-8"
    )
    with pytest.raises(ValidationError, match="no rows"):
        validate(good_artifacts)


def test_missing_generated_at_fails(good_artifacts):
    (good_artifacts / "generated_at.txt").unlink()
    with pytest.raises(ValidationError, match="generated_at"):
        validate(good_artifacts)


def test_cli_success_exits_zero(good_artifacts):
    r = subprocess.run(
        [sys.executable, "-m", "scripts.validate_artifacts", str(good_artifacts)],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr


def test_cli_failure_exits_nonzero(good_artifacts):
    (good_artifacts / "clean_posts.csv").unlink()
    r = subprocess.run(
        [sys.executable, "-m", "scripts.validate_artifacts", str(good_artifacts)],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    assert r.returncode != 0
    assert "missing" in (r.stdout + r.stderr).lower()
