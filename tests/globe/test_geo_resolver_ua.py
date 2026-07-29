# tests/globe/test_geo_resolver_ua.py
"""GLOBE_NOMINATIM_UA guard (Phase 5 Task 2).

Nominatim's usage policy requires a contactable User-Agent. `geo_resolver.py`
lives under `backend/globe`, a separate `app`-rooted package that isn't on
sys.path by default when running the root test suite -- add it before import,
same pattern as tests/etl/test_pipeline.py does for `backend`.
"""
import sys
import warnings
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend" / "globe"))

# app.config's `class Config:` (pydantic-settings v1 style) emits a
# PydanticDeprecatedSince20 warning at class-definition time, on every import
# of app.config -- pre-existing, unrelated to the UA guard under test here.
# Suppress just for this import so `-W error` (used by CI/verification runs)
# doesn't fail collection over it.
with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from app.pipeline.geo_resolver import get_nominatim_user_agent  # noqa: E402


def test_missing_env_raises(monkeypatch):
    monkeypatch.delenv("GLOBE_NOMINATIM_UA", raising=False)
    with pytest.raises(RuntimeError, match="GLOBE_NOMINATIM_UA"):
        get_nominatim_user_agent()


def test_generic_env_raises(monkeypatch):
    monkeypatch.setenv("GLOBE_NOMINATIM_UA", "changeme")
    with pytest.raises(RuntimeError, match="generic"):
        get_nominatim_user_agent()


def test_valid_env_returns_without_raising(monkeypatch):
    ua = "narrativesignal-globe/1.0 by u/x (contact: y@z)"
    monkeypatch.setenv("GLOBE_NOMINATIM_UA", ua)
    assert get_nominatim_user_agent() == ua
