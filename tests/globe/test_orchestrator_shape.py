"""Phase 5 Task 3 -- Globe pipeline orchestrator shape test.

The orchestrator (``backend/globe/app/pipeline/orchestrator.py::run_pipeline``)
glues ten pipeline stages together: Reddit ingestion, fast geo filter, LLM geo
resolution + Nominatim, LLM event structuring, external news correlation, LLM
intelligence analysis, deterministic impact scoring, deterministic clustering,
and finally emits ``global_events_<date>.json`` for the Globe frontend to
render.

A real run makes ~140 Groq calls plus Nominatim traffic -- unrunnable in CI --
so this test:

* Mocks every EXTERNAL boundary (Reddit fetch, LLM chat, geocoder, news APIs,
  DB writes) with in-memory canned data / no-ops.
* Runs the orchestrator's own glue logic (fast_geo_filter, score_all_events,
  cluster_events, _build_map_events, _save_output) FOR REAL.
* Asserts the emitted JSON matches the schema in design spec sec 4.3 that the
  Globe frontend consumes.

This is a SHAPE test, not a golden-master -- it verifies types and required
fields, not specific values.
"""
from __future__ import annotations

import asyncio
import json
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Tuple

import pytest

# The globe backend lives in backend/globe and expects "app.*" imports.
_GLOBE_ROOT = Path(__file__).resolve().parents[2] / "backend" / "globe"
if str(_GLOBE_ROOT) not in sys.path:
    sys.path.insert(0, str(_GLOBE_ROOT))

# app.config's ``class Config`` (pydantic-settings v1 style) emits a
# PydanticDeprecatedSince20 warning at class-definition time. Pre-existing,
# unrelated to this test; suppress just for these imports so ``-W error``
# doesn't fail collection.
with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from app.database import connection as db_connection  # noqa: E402
    from app.database.models import (  # noqa: E402
        GeoCandidate,
        NarrativeIntel,
        NewsBundle,
        RawPost,
        ResolvedLocation,
        StructuredEvent,
    )
    from app.pipeline import orchestrator  # noqa: E402


# ---------------------------------------------------------------- canned data
# Four posts, three subreddits. Every one carries an obvious geo term so the
# fast_geo_filter -- which we let run FOR REAL -- keeps them all. Engagement
# scores are all well above the 500 threshold that lets Layer 5's no-news
# fallback keep events alive when the mocked news correlator returns empty.

_CREATED = 1_738_000_000.0

_CANNED_POSTS: List[RawPost] = [
    RawPost(
        id="post_kyiv",
        title="Meeting in Kyiv discusses ceasefire terms",
        subreddit="worldnews",
        score=2500,
        num_comments=800,
        created_utc=_CREATED,
        author="reporter1",
        url="https://reddit.com/r/worldnews/1",
        engagement_score=1820.0,
        velocity_score=250.0,
    ),
    RawPost(
        id="post_tehran",
        title="Iran launches operation from Tehran targeting Israel border",
        subreddit="geopolitics",
        score=3000,
        num_comments=1200,
        created_utc=_CREATED + 500,
        author="reporter2",
        url="https://reddit.com/r/geopolitics/1",
        engagement_score=2280.0,
        velocity_score=400.0,
    ),
    RawPost(
        id="post_ankara",
        title="Turkey brokers deal with Russia over gas transit from Ankara",
        subreddit="worldnews",
        score=1500,
        num_comments=500,
        created_utc=_CREATED + 1000,
        author="reporter3",
        url="https://reddit.com/r/worldnews/2",
        engagement_score=1100.0,
        velocity_score=180.0,
    ),
    RawPost(
        id="post_kyiv_dup",
        title="Kyiv officials respond to new pressure from Moscow",
        subreddit="geopolitics",
        score=1800,
        num_comments=600,
        created_utc=_CREATED + 1500,
        author="reporter4",
        url="https://reddit.com/r/geopolitics/2",
        engagement_score=1320.0,
        velocity_score=200.0,
    ),
]


# Fixed lat/lon per location keyword so clustering (which groups by
# ``primary_location``) collapses the two Kyiv posts into one cluster.
_GEO_TABLE = {
    "Kyiv, Ukraine":  (50.4501,  30.5234, "city",    "Kyiv, Ukraine"),
    "Tehran, Iran":   (35.6892,  51.3890, "city",    "Tehran, Iran"),
    "Ankara, Turkey": (39.9334,  32.8597, "city",    "Ankara, Turkey"),
}

# Post-title-substring -> resolved location name. Kept explicit so we don't
# depend on the LLM prompt shape.
_TITLE_TO_LOCATION = {
    "Kyiv":   "Kyiv, Ukraine",
    "Tehran": "Tehran, Iran",
    "Ankara": "Ankara, Turkey",
}


def _location_for(candidate: GeoCandidate) -> str:
    for needle, name in _TITLE_TO_LOCATION.items():
        if needle in candidate.title:
            return name
    return "Kyiv, Ukraine"  # deterministic fallback -- shouldn't be hit


# ------------------------------------------------------------------- mocks --
async def _fake_fetch_reddit_posts() -> List[RawPost]:
    return list(_CANNED_POSTS)


def _fake_persist_raw_posts(_posts: List[RawPost]) -> None:
    return None


def _fake_get_existing_posts_for_today() -> List[RawPost]:
    return []


async def _fake_resolve_all_candidates(
    candidates: List[GeoCandidate],
) -> List[Tuple[GeoCandidate, ResolvedLocation]]:
    pairs: List[Tuple[GeoCandidate, ResolvedLocation]] = []
    for c in candidates:
        name = _location_for(c)
        lat, lon, gtype, display = _GEO_TABLE[name]
        pairs.append((
            c,
            ResolvedLocation(
                name=name, lat=lat, lon=lon,
                geo_type=gtype, display_name=display,
            ),
        ))
    return pairs


async def _fake_structure_all_events(
    pairs: List[Tuple[GeoCandidate, ResolvedLocation]],
) -> List[StructuredEvent]:
    events: List[StructuredEvent] = []
    for i, (cand, loc) in enumerate(pairs):
        events.append(StructuredEvent(
            id=f"event_{i}_{cand.post_id}",
            post_id=cand.post_id,
            event_type="conflict",
            primary_location=loc.name,
            secondary_locations=[],
            key_entities=["Government", "Military"],
            search_queries=[f"{loc.name} news", f"{loc.name} update"],
        ))
    return events


async def _fake_correlate_all_events(
    events: List[StructuredEvent],
) -> List[NewsBundle]:
    # Return one empty bundle per event. Layer 5 filter drops no-news events
    # UNLESS the source post's engagement_score >= 500 -- our canned posts
    # all satisfy that, so every event survives via the fallback branch.
    return [NewsBundle(event_id=e.id, articles=[], news_count=0) for e in events]


async def _fake_analyse_all_events(
    _posts_map,
    events: List[StructuredEvent],
    _bundles_map,
) -> List[NarrativeIntel]:
    return [
        NarrativeIntel(
            event_id=e.id,
            summary=f"Executive summary for {e.primary_location}.",
            sentiment_label="negative",
            sentiment_confidence=0.8,
            risk_level="High",
            strategic_implications=[
                "Implication A",
                "Implication B",
            ],
        )
        for e in events
    ]


# ------------------------------------------------------------------ fixture -
@pytest.fixture()
def wired(tmp_path, monkeypatch):
    """Wire every external boundary + point output at a scratch dir."""
    # Nominatim UA guard fires eagerly at Layer 3 unless this is set. We
    # replace resolve_all_candidates so the guard is never reached, but set it
    # anyway as belt-and-braces in case future refactors move the call.
    monkeypatch.setenv(
        "GLOBE_NOMINATIM_UA",
        "narrativesignal-globe/1.0 (contact: test@test.com)",
    )

    # Point _save_output at the scratch dir by returning a stub settings
    # from ``get_settings`` -- _save_output looks up DATA_DIR on that object.
    class _StubSettings:
        DATA_DIR = str(tmp_path)

    monkeypatch.setattr(orchestrator, "get_settings", lambda: _StubSettings())

    # Mock every external boundary via the reference the orchestrator holds.
    monkeypatch.setattr(orchestrator, "fetch_reddit_posts", _fake_fetch_reddit_posts)
    monkeypatch.setattr(orchestrator, "persist_raw_posts", _fake_persist_raw_posts)
    monkeypatch.setattr(
        orchestrator,
        "get_existing_posts_for_today",
        _fake_get_existing_posts_for_today,
    )
    monkeypatch.setattr(orchestrator, "resolve_all_candidates", _fake_resolve_all_candidates)
    monkeypatch.setattr(orchestrator, "structure_all_events", _fake_structure_all_events)
    monkeypatch.setattr(orchestrator, "correlate_all_events", _fake_correlate_all_events)
    monkeypatch.setattr(orchestrator, "analyse_all_events", _fake_analyse_all_events)

    # cluster_events calls _persist_clusters -> DuckDB. Stub it so we never
    # touch the DB (and don't need to init it).
    from app.pipeline import clustering as clustering_mod
    monkeypatch.setattr(clustering_mod, "_persist_clusters", lambda _c: None)

    # Defensive: ensure no stale connection lingers if something did try DB.
    monkeypatch.setattr(db_connection, "_conn", None, raising=False)

    return tmp_path


# --------------------------------------------------------------- the test --
_REQUIRED_TOP_LEVEL = {"run_id", "generated_at", "event_count", "events"}
_REQUIRED_EVENT_KEYS = {
    "id", "title", "locations", "timestamp", "impact_score", "sentiment",
    "risk_level", "subreddit", "reddit_post_ids", "reddit_metrics",
    "news_sources", "confidence", "summary",
}
_REQUIRED_LOCATION_KEYS = {"name", "lat", "lon", "geo_type", "display_name"}
_VALID_SENTIMENTS = {"negative", "neutral", "mixed", "positive"}
_VALID_RISK = {"Low", "Medium", "High"}
_VALID_CONFIDENCE = {"Low", "Medium", "High"}


def _assert_iso8601(value: str, field: str) -> None:
    """Parse the value with datetime.fromisoformat -- must not raise."""
    assert isinstance(value, str) and value, f"{field} must be a non-empty string"
    # datetime.fromisoformat accepts the trailing 'Z' only on 3.11+; the
    # orchestrator emits timezone-aware isoformat() which is always parseable.
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as e:  # pragma: no cover -- surfaced as assertion
        raise AssertionError(f"{field} is not valid ISO8601: {value!r} ({e})")


def test_run_pipeline_writes_frontend_shaped_json(wired):
    """Run the orchestrator end-to-end (mocked) and validate the output JSON.

    The globe frontend consumes the schema in
    ``docs/superpowers/specs/2026-07-28-live-data-layer-design.md`` sec 4.3.
    Any drift here breaks pin rendering at
    ``frontend/app/globe/page.jsx:215`` (finite lat/lon check) or the event
    detail panel. This test locks the shape.
    """
    tmp_path = wired

    status = asyncio.run(orchestrator.run_pipeline(run_id="shape-test"))

    # ── Pipeline itself completed ──────────────────────────────────────────
    assert status.status == "complete", (
        f"pipeline did not complete: status={status.status!r} "
        f"error={status.error!r}"
    )
    assert status.posts_ingested == len(_CANNED_POSTS)
    assert status.geo_candidates >= 1, "fast_geo_filter dropped every post"
    assert status.events_structured >= 1
    assert status.clusters_built >= 1

    # ── Output JSON exists in the scratch dir ─────────────────────────────
    outputs = sorted(Path(tmp_path).glob("global_events_*.json"))
    assert len(outputs) == 1, (
        f"expected exactly one global_events_*.json in {tmp_path}, "
        f"got: {[p.name for p in outputs]}"
    )
    payload = json.loads(outputs[0].read_text(encoding="utf-8"))

    # ── Top-level schema (spec sec 4.3) ────────────────────────────────────
    missing_top = _REQUIRED_TOP_LEVEL - payload.keys()
    assert not missing_top, f"missing top-level keys: {missing_top}"

    assert isinstance(payload["run_id"], str) and payload["run_id"]
    _assert_iso8601(payload["generated_at"], "generated_at")
    assert isinstance(payload["event_count"], int)
    assert isinstance(payload["events"], list)
    assert payload["event_count"] == len(payload["events"]), (
        "event_count must equal len(events)"
    )
    assert payload["events"], "expected at least one event in output"

    # Kyiv appears in two canned posts -> clustering (grouped by
    # primary_location) must collapse them into a single cluster/event.
    # The other two events (Tehran, Ankara) stay distinct, so the total is 3.
    assert len(payload["events"]) == 3, (
        "clustering should collapse the two Kyiv posts into one event; "
        f"got {len(payload['events'])} events"
    )

    # ── Per-event schema ───────────────────────────────────────────────────
    for i, ev in enumerate(payload["events"]):
        ctx = f"event[{i}] id={ev.get('id')!r}"

        missing = _REQUIRED_EVENT_KEYS - ev.keys()
        assert not missing, f"{ctx} missing keys: {missing}"

        assert isinstance(ev["id"], str) and ev["id"], f"{ctx}: id must be non-empty str"
        assert isinstance(ev["title"], str) and ev["title"], f"{ctx}: title must be non-empty str"

        # locations: non-empty list, each entry has all five spec keys and
        # finite lat/lon (frontend drops the pin otherwise).
        assert isinstance(ev["locations"], list) and ev["locations"], (
            f"{ctx}: locations must be a non-empty list"
        )
        for j, loc in enumerate(ev["locations"]):
            lctx = f"{ctx} locations[{j}]"
            loc_missing = _REQUIRED_LOCATION_KEYS - loc.keys()
            assert not loc_missing, f"{lctx} missing keys: {loc_missing}"
            assert isinstance(loc["name"], str) and loc["name"], f"{lctx}: name required"
            assert isinstance(loc["lat"], (int, float)), f"{lctx}: lat must be numeric"
            assert isinstance(loc["lon"], (int, float)), f"{lctx}: lon must be numeric"
            # finite check -- frontend page.jsx:215 drops non-finite pins.
            import math
            assert math.isfinite(loc["lat"]) and math.isfinite(loc["lon"]), (
                f"{lctx}: lat/lon must be finite (frontend drops non-finite pins)"
            )
            assert isinstance(loc["geo_type"], str), f"{lctx}: geo_type must be str"
            assert isinstance(loc["display_name"], str), f"{lctx}: display_name must be str"

        _assert_iso8601(ev["timestamp"], f"{ctx}.timestamp")

        assert isinstance(ev["impact_score"], (int, float)), f"{ctx}: impact_score numeric"
        assert 0.0 <= float(ev["impact_score"]) <= 1.0, (
            f"{ctx}: impact_score must be in [0,1], got {ev['impact_score']}"
        )

        assert ev["sentiment"] in _VALID_SENTIMENTS, (
            f"{ctx}: sentiment={ev['sentiment']!r} not in {_VALID_SENTIMENTS}"
        )
        assert ev["risk_level"] in _VALID_RISK, (
            f"{ctx}: risk_level={ev['risk_level']!r} not in {_VALID_RISK}"
        )
        assert ev["confidence"] in _VALID_CONFIDENCE, (
            f"{ctx}: confidence={ev['confidence']!r} not in {_VALID_CONFIDENCE}"
        )

        assert isinstance(ev["subreddit"], str), f"{ctx}: subreddit must be str"

        assert isinstance(ev["reddit_post_ids"], list), f"{ctx}: reddit_post_ids list"
        for pid in ev["reddit_post_ids"]:
            assert isinstance(pid, str), f"{ctx}: reddit_post_ids entries must be str"

        # reddit_metrics -- spec sec 4.3 requires these four sub-keys.
        rm = ev["reddit_metrics"]
        assert isinstance(rm, dict), f"{ctx}: reddit_metrics must be dict"
        for sub in ("total_score", "total_comments", "post_count", "subreddits"):
            assert sub in rm, f"{ctx}.reddit_metrics missing {sub!r}"
        assert isinstance(rm["total_score"], int), f"{ctx}.reddit_metrics.total_score int"
        assert isinstance(rm["total_comments"], int), f"{ctx}.reddit_metrics.total_comments int"
        assert isinstance(rm["post_count"], int), f"{ctx}.reddit_metrics.post_count int"
        assert isinstance(rm["subreddits"], list), f"{ctx}.reddit_metrics.subreddits list"

        # news_sources: list of dicts with the four spec keys. Max 5 per spec.
        assert isinstance(ev["news_sources"], list), f"{ctx}: news_sources must be list"
        assert len(ev["news_sources"]) <= 5, (
            f"{ctx}: news_sources exceeds spec max of 5"
        )
        for k, art in enumerate(ev["news_sources"]):
            actx = f"{ctx} news_sources[{k}]"
            for sub in ("title", "url", "source", "trusted"):
                assert sub in art, f"{actx} missing {sub!r}"

        assert isinstance(ev["summary"], str), f"{ctx}: summary must be str"
