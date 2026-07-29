"""
Task 1 regression test: the globe events endpoint MUST NOT fall back to hardcoded
mock events (Tel Aviv / Tehran / Kyiv / ...) when no pipeline output JSON exists.

If the pipeline never ran (or its output is missing), the API must surface that
visibly by returning an empty events list with an explicit ``reason`` field.
The convincing 6-event mock fallback (landmine L13) let the pipeline silently
break in production without anyone noticing — hence this test.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# The globe backend lives in backend/globe and expects "app.*" imports.
_GLOBE_ROOT = Path(__file__).resolve().parents[2] / "backend" / "globe"
if str(_GLOBE_ROOT) not in sys.path:
    sys.path.insert(0, str(_GLOBE_ROOT))

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

# Importing the router pulls in the pipeline stack; skip if any hard dep is
# missing so the assertion below only fires when the environment can actually
# exercise the route.
events_module = pytest.importorskip("app.api.routes.events")


_MOCK_MARKERS = ("Tel Aviv", "Tehran", "Kyiv", "mock-tel-aviv", "mock-tehran", "mock-kyiv")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    # Point the pipeline output loader at an empty directory so no JSON exists.
    empty_data_dir = tmp_path / "no_pipeline_output"
    empty_data_dir.mkdir()

    # The route calls ``load_latest_output`` imported into events.py; force it
    # to return None as if no ``global_events_<date>.json`` was found.
    monkeypatch.setattr(events_module, "load_latest_output", lambda: None)

    app = fastapi.FastAPI()
    app.include_router(events_module.router)
    with TestClient(app) as c:
        yield c


def test_get_events_returns_empty_with_reason_when_no_pipeline_output(client):
    """No pipeline JSON -> empty events + explicit reason, NEVER the mock 6."""
    resp = client.get("/events/")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body.get("events") == [], (
        "events endpoint must return an empty list when no pipeline output "
        f"exists, got: {body.get('events')!r}"
    )
    assert body.get("total") == 0

    # An explicit machine-readable reason must surface that data is missing.
    reason = body.get("reason")
    assert reason, "response must include a non-empty 'reason' when no data exists"
    assert "no data" in reason.lower()

    # Belt-and-braces: absolutely no trace of the hardcoded mock cities.
    serialized = resp.text
    for marker in _MOCK_MARKERS:
        assert marker not in serialized, (
            f"mock fallback leaked into response: found {marker!r}. "
            "The hardcoded Tel Aviv/Tehran/Kyiv fallback (landmine L13) must be gone."
        )


def test_mock_events_constant_is_gone_from_module():
    """Defensive: the _MOCK_EVENTS constant itself must not exist anymore."""
    assert not hasattr(events_module, "_MOCK_EVENTS"), (
        "_MOCK_EVENTS constant still defined in events.py — delete it, don't "
        "just stop calling it."
    )
    assert not hasattr(events_module, "_mock_data_payload"), (
        "_mock_data_payload() helper still defined in events.py — delete it."
    )
