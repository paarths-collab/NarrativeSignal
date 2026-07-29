"""Fake asyncpraw for offline, deterministic tests."""
import json
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, Iterable

import pytest


FIXTURE_DIR = Path(__file__).parent / "fixtures"


@dataclass
class FakeSubmission:
    """Mirrors the subset of asyncpraw.models.Submission the fetcher reads."""
    id: str
    created_utc: float
    subreddit: str
    author: str
    title: str
    domain: str
    score: int
    num_comments: int
    permalink: str
    url: str
    raw: dict  # the whole 'data' payload, for serialize.py

    @classmethod
    def from_raw(cls, raw: dict) -> "FakeSubmission":
        return cls(
            id=raw["id"], created_utc=raw["created_utc"], subreddit=raw["subreddit"],
            author=raw["author"], title=raw["title"], domain=raw["domain"],
            score=raw["score"], num_comments=raw["num_comments"],
            permalink=raw["permalink"], url=raw["url"], raw=raw,
        )


class FakeSubreddit:
    def __init__(self, name: str, canned: list[dict]):
        self.display_name = name
        self._canned = canned

    async def new(self, *, limit: int = 100) -> AsyncIterator[FakeSubmission]:
        for entry in self._canned[:limit]:
            yield FakeSubmission.from_raw(entry["data"])

    async def top(self, *, time_filter: str = "day", limit: int = 100) -> AsyncIterator[FakeSubmission]:
        # Same fixture, different (partial) ordering — good enough to exercise dedupe.
        for entry in reversed(self._canned[:limit]):
            yield FakeSubmission.from_raw(entry["data"])


class FakeReddit:
    def __init__(self, canned_by_sub: dict[str, list[dict]]):
        self._canned = canned_by_sub

    async def subreddit(self, name: str) -> FakeSubreddit:
        return FakeSubreddit(name, self._canned.get(name, []))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


@pytest.fixture(scope="session")
def canned_page() -> list[dict]:
    return json.loads((FIXTURE_DIR / "canned_new_page.json").read_text())["data"]["children"]


@pytest.fixture()
def fake_reddit(canned_page) -> FakeReddit:
    """Every subreddit serves the same 20 records — enough to exercise dedupe."""
    from fetcher.config import SUBREDDITS
    return FakeReddit({name: canned_page for name in SUBREDDITS})
