import asyncio
from datetime import datetime, timezone

import pytest

from fetcher.fetch import fetch_day, merge_and_dedupe


def _sub(id_, ts, subreddit="politics", author="u", domain="self.politics"):
    """Minimal record shape that mirrors what our fake Submission.raw carries."""
    return {
        "id": id_, "created_utc": ts, "subreddit": subreddit, "author": author,
        "title": id_, "domain": domain, "score": 1, "num_comments": 0,
        "permalink": f"/r/{subreddit}/comments/{id_}/", "url": "u",
    }


def test_dedupe_by_id_preserves_new_first():
    a = [_sub("x", 1.0), _sub("y", 2.0)]
    b = [_sub("y", 2.0), _sub("z", 3.0)]     # 'y' repeats
    out = merge_and_dedupe(a, b)
    assert [r["id"] for r in out] == ["x", "y", "z"]


def test_dedupe_ties_prefer_higher_score():
    a = [{**_sub("x", 1.0), "score": 3}]
    b = [{**_sub("x", 1.0), "score": 99}]    # top? results usually updated
    assert merge_and_dedupe(a, b)[0]["score"] == 99


def test_date_filter_is_utc(fake_reddit):
    """Every canned record is Feb 2025; asking for Feb 17 UTC must return only those."""
    day = datetime(2025, 2, 17, tzinfo=timezone.utc).date()
    got = asyncio.run(fetch_day(fake_reddit, day=day))
    assert got, "canned fixture must have some Feb-17-UTC records"
    for r in got:
        ts = datetime.fromtimestamp(r["created_utc"], tz=timezone.utc)
        assert ts.date() == day, f"{r['id']} at {ts} leaked out of window"


def test_returns_raw_dict_not_submission(fake_reddit):
    day = datetime(2025, 2, 17, tzinfo=timezone.utc).date()
    got = asyncio.run(fetch_day(fake_reddit, day=day))
    for r in got:
        assert isinstance(r, dict) and "domain" in r and "created_utc" in r


def test_end_to_end_reads_through_normalize(tmp_path, fake_reddit):
    """The whole plan's promise: output feeds etl/normalize.py without any translation."""
    from datetime import date
    from fetcher.serialize import write_jsonl
    from etl.normalize import normalize_listing

    day = date(2025, 2, 17)
    records = asyncio.run(fetch_day(fake_reddit, day=day))
    path = tmp_path / "day.jsonl"
    write_jsonl(records, path)
    df = normalize_listing(path)
    assert len(df) > 0
    assert set(df.columns) == {"id", "created_datetime", "subreddit", "author",
                               "title", "domain", "score", "num_comments",
                               "permalink", "url"}


def test_one_subreddit_403_does_not_kill_the_run(canned_page):
    """If r/Republican goes private for a day, the other 9 must still land."""
    from asyncprawcore.exceptions import Forbidden
    from fetcher.config import SUBREDDITS

    class PartialReddit:
        async def subreddit(self, name):
            if name == "Republican":
                raise Forbidden(_mock_response(403))
            from tests.fetcher.conftest import FakeSubreddit
            return FakeSubreddit(name, canned_page)

        async def __aenter__(self): return self
        async def __aexit__(self, *_): return False

    from datetime import date
    got = asyncio.run(fetch_day(PartialReddit(), day=date(2025, 2, 17)))
    subs = {r["subreddit"] for r in got}
    assert "Republican" not in subs
    assert len(subs) >= 8  # spec §8: proceed if ≥8/10 succeeded


def _mock_response(status):
    class R:
        def __init__(self, s): self.status = s
    return R(status)
