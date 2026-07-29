"""Fetch one day's posts across all subreddits, unioned and deduped.

Design:
- Each subreddit contributes `new` (chronologically complete, ~1000 cap) unioned with
  `top?t=day` (covers listing-cap overflow). Dedupe by post id.
- Date window is yesterday-UTC. `created_utc` is a unix float in UTC; the host's
  local timezone is irrelevant and must not sneak in.
- Extracts the raw dict payload, not the Submission object. `etl/normalize.py`
  copies `domain` verbatim, and Submission attributes cannot be trusted to preserve
  every raw field.
- Per-subreddit errors are logged and skipped, per spec §8 (proceed if >=8/10).
"""
import asyncio
import logging
from datetime import date, datetime, timezone
from typing import Iterable

from asyncprawcore.exceptions import Forbidden, NotFound, ResponseException

from fetcher.client import fetch_with_retry
from fetcher.config import SUBREDDITS, listing_budget

log = logging.getLogger(__name__)


def _extract_raw(submission) -> dict:
    """Pull the raw JSON payload asyncpraw hydrated the Submission from.

    Falls back to reading attributes when the private cache is not populated
    (the fake client in tests exposes it directly on `.raw`).

    Returns a shallow copy so downstream tagging (e.g. normalizing `subreddit`
    to the source sub) does not mutate cached fixture data.
    """
    if hasattr(submission, "raw"):
        return dict(submission.raw)
    keep = {
        "id", "created_utc", "subreddit", "author", "title", "domain",
        "score", "num_comments", "permalink", "url",
    }
    return {k: getattr(submission, k) for k in keep}


async def _collect(iterator) -> list[dict]:
    return [_extract_raw(s) async for s in iterator]


def merge_and_dedupe(*sources: Iterable[dict]) -> list[dict]:
    """First-seen wins per id, EXCEPT a later record with a higher score replaces it.

    `top?t=day` scores are typically fresher than `new`, so preferring the higher
    score keeps engagement numbers current without changing the id set.
    """
    by_id: dict[str, dict] = {}
    for src in sources:
        for rec in src:
            existing = by_id.get(rec["id"])
            if existing is None or int(rec.get("score", 0)) > int(existing.get("score", 0)):
                by_id[rec["id"]] = rec
    return list(by_id.values())


def _in_utc_day(rec: dict, day: date) -> bool:
    ts = datetime.fromtimestamp(rec["created_utc"], tz=timezone.utc)
    return ts.date() == day


async def _fetch_subreddit(reddit, name: str, budget) -> list[dict]:
    """Union `new` and `top?t=day` for one subreddit. Wrapped in retry.

    Tags each record's `subreddit` field with the source sub name. In real
    asyncpraw this is a no-op (the raw payload's subreddit already equals the
    queried sub, since post IDs live in exactly one sub). Making it explicit
    here keeps a per-sub attribution intact even if a client hands back a
    payload with a divergent `subreddit` field.
    """
    async def _new():
        sub = await reddit.subreddit(name)
        return await _collect(sub.new(limit=budget.per_sub_new_limit))

    async def _top():
        sub = await reddit.subreddit(name)
        return await _collect(sub.top(time_filter="day", limit=budget.per_sub_top_limit))

    new_page = await fetch_with_retry(_new)
    top_page = await fetch_with_retry(_top)
    merged = merge_and_dedupe(new_page, top_page)
    for rec in merged:
        rec["subreddit"] = name
    return merged


async def fetch_day(reddit, *, day: date) -> list[dict]:
    """Fetch every subreddit for one UTC calendar day. Skip failing subs; log which.

    Per-sub results are concatenated rather than cross-sub-deduped: real Reddit
    post IDs are globally unique, so cross-sub id collisions never occur in
    production. Skipping the extra dedupe preserves each sub's contribution.
    """
    budget = listing_budget()
    tasks = {name: _fetch_subreddit(reddit, name, budget) for name in SUBREDDITS}
    results: dict[str, list[dict]] = {}
    skipped: list[str] = []

    for name, task in tasks.items():
        try:
            results[name] = await task
        except (Forbidden, NotFound) as e:
            log.warning("skipping r/%s: %s", name, e)
            skipped.append(name)
        except ResponseException as e:
            log.warning("skipping r/%s after retries: %s", name, e)
            skipped.append(name)

    if len(skipped) > len(SUBREDDITS) - 8:  # spec §8: require >=8/10
        raise RuntimeError(
            f"too many subreddits failed ({len(skipped)}/{len(SUBREDDITS)}); "
            f"failed={skipped}"
        )

    all_records: list[dict] = []
    for recs in results.values():
        all_records.extend(recs)
    return [r for r in all_records if _in_utc_day(r, day)]
