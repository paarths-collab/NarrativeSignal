# tests/globe/test_ua_wire.py
"""Follow-up to P5 T2 (commit 321ea1c): the guard in
`app.pipeline.geo_resolver.get_nominatim_user_agent()` fires correctly, but the
actual outgoing Nominatim requests were still hardcoded to a fixed User-Agent
string in two places:

  - app/utils/geocoder.py       (forward geocoding used by the pipeline)
  - app/api/routes/events.py    (reverse geocoding in the /events/news route)

These tests assert the wire request now carries `GLOBE_NOMINATIM_UA` (not the
old hardcoded string), and that unsetting/genericizing the env still raises the
same RuntimeError from the shared guard -- i.e. no Nominatim traffic goes out
without a valid, contactable UA.
"""
from __future__ import annotations

import asyncio
import sys
import warnings
from pathlib import Path

import httpx
import pytest

# The globe backend lives in backend/globe and expects "app.*" imports, same
# pattern as tests/globe/test_geo_resolver_ua.py and test_events_route.py.
REPO_ROOT = Path(__file__).resolve().parents[2]
GLOBE_ROOT = REPO_ROOT / "backend" / "globe"
if str(GLOBE_ROOT) not in sys.path:
    sys.path.insert(0, str(GLOBE_ROOT))

# app.config's `class Config:` (pydantic-settings v1 style) emits a
# PydanticDeprecatedSince20 warning at class-definition time, on every import
# of app.config (pulled in transitively via app.utils.logger) -- pre-existing,
# unrelated to the UA wiring under test here. Suppress just for this import so
# `-W error` doesn't fail collection over it.
with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from app.utils import geocoder  # noqa: E402

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

events_module = pytest.importorskip("app.api.routes.events")

_OLD_HARDCODED_UAS = {"simppl-globe/1.0", "narrativesignal-globe/1.0"}


class _FakeJsonResponse:
    """Minimal stand-in for httpx.Response: only .json() (sync) is used."""

    def __init__(self, data, status_code: int = 200):
        self._data = data
        self.status_code = status_code

    def json(self):
        return self._data


# ── geocoder.py (forward geocoding) ─────────────────────────────────────────

def test_geocode_location_sends_env_user_agent(monkeypatch):
    """geocode_location() must send GLOBE_NOMINATIM_UA, not the old hardcode."""
    ua = "narrativesignal-globe/1.0 by u/tester (contact: dev@example.com)"
    monkeypatch.setenv("GLOBE_NOMINATIM_UA", ua)
    geocoder._geo_cache.clear()

    captured = {}

    async def fake_get(self, url, params=None, headers=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        return _FakeJsonResponse([])  # empty result is fine; we only need the headers

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    asyncio.run(geocoder.geocode_location("Springfield"))

    assert "nominatim" in captured["url"]
    assert captured["headers"]["User-Agent"] == ua
    assert captured["headers"]["User-Agent"] not in _OLD_HARDCODED_UAS


def test_geocode_location_raises_without_valid_env(monkeypatch):
    """No/generic GLOBE_NOMINATIM_UA must raise before any HTTP request is made."""
    monkeypatch.delenv("GLOBE_NOMINATIM_UA", raising=False)
    geocoder._geo_cache.clear()

    async def fake_get(self, url, params=None, headers=None, timeout=None):
        pytest.fail("Nominatim request must not be sent without a valid User-Agent")

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    with pytest.raises(RuntimeError, match="GLOBE_NOMINATIM_UA"):
        asyncio.run(geocoder.geocode_location("Shelbyville"))


def test_geocode_location_raises_on_generic_env(monkeypatch):
    """A generic-looking UA must also raise, same as the guard's own tests."""
    monkeypatch.setenv("GLOBE_NOMINATIM_UA", "changeme")
    geocoder._geo_cache.clear()

    async def fake_get(self, url, params=None, headers=None, timeout=None):
        pytest.fail("Nominatim request must not be sent with a generic User-Agent")

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    with pytest.raises(RuntimeError, match="generic"):
        asyncio.run(geocoder.geocode_location("Ogdenville"))


# ── events.py (/events/news reverse geocoding) ──────────────────────────────

def test_events_news_route_sends_env_user_agent(monkeypatch):
    """The /events/news reverse-geocode call must carry GLOBE_NOMINATIM_UA."""
    ua = "narrativesignal-globe/1.0 by u/tester (contact: dev@example.com)"
    monkeypatch.setenv("GLOBE_NOMINATIM_UA", ua)
    monkeypatch.setattr(events_module, "load_latest_output", lambda: None)

    captured = {}

    async def fake_get(self, url, params=None, headers=None, timeout=None):
        if "nominatim" in url:
            captured["headers"] = headers
        return _FakeJsonResponse({"address": {}})

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    app = fastapi.FastAPI()
    app.include_router(events_module.router)
    with TestClient(app) as client:
        resp = client.post("/events/news", json={"latitude": 10.0, "longitude": 20.0})

    assert resp.status_code == 200, resp.text
    assert "headers" in captured, "reverse-geocode request was never made"
    assert captured["headers"]["User-Agent"] == ua
    assert captured["headers"]["User-Agent"] not in _OLD_HARDCODED_UAS


def test_events_news_route_raises_without_valid_env(monkeypatch):
    """No GLOBE_NOMINATIM_UA -> the route must fail fast, not silently degrade."""
    monkeypatch.delenv("GLOBE_NOMINATIM_UA", raising=False)
    monkeypatch.setattr(events_module, "load_latest_output", lambda: None)

    async def fake_get(self, url, params=None, headers=None, timeout=None):
        pytest.fail("Nominatim request must not be sent without a valid User-Agent")

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    app = fastapi.FastAPI()
    app.include_router(events_module.router)
    with TestClient(app, raise_server_exceptions=True) as client:
        with pytest.raises(RuntimeError, match="GLOBE_NOMINATIM_UA"):
            client.post("/events/news", json={"latitude": 10.0, "longitude": 20.0})
