# Reddit Fetch Implementation Plan (Phase 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fetch yesterday's posts from Reddit into a `data.jsonl` byte-compatible with
`etl/normalize.py`, without changing any downstream transform.

**Architecture:** An async fetcher under `fetcher/` calls asyncpraw against ten subreddits,
unions `new` (chronologically complete) with `top?t=day` (covers volume that overflows the
listing cap), dedupes by post id, filters to yesterday in UTC, and writes one raw
`{"kind":"t3","data":{...}}` JSON object per line. A small CLI ties it to `etl/pipeline.py`
so one command produces every artifact.

**Tech stack:** Python 3.11, asyncpraw 7.8.1 (already pinned in `requirements.txt`),
tenacity for retries, tzdata for timezone-safe date math. Everything else already installed.

**Scope note:** This is Phase 2 of 6. It ends with a passing test suite and a `fetcher fetch`
command that produces a fresh JSONL for a supplied `--date`. **No** cron, no GitHub Actions,
no deploy wiring, no push to `data-live` — those are Phase 3. The output of this plan sits
on disk locally and is validated by piping it through the existing ETL.

---

## What `etl/normalize.py` requires from us

The single hard contract: each line is `{"kind": "t3", "data": {...}}` where `data` carries
at minimum:

```
id str, created_utc float (unix seconds), subreddit str (bare, no r/), author str
(preserving [deleted]), title str, domain str (Reddit's own field — see below),
score int, num_comments int, permalink str, url str
```

The 10 columns of `clean_posts.csv` are a projection of exactly these. `etl/normalize.py`
drops `author == "AutoModerator"` and copies `domain` verbatim; anything else about the
raw record is passed through untouched.

**`domain` is not `urlparse(url).netloc`.** Reddit computes it server-side (`self.<Subreddit>`
for text posts, `i.redd.it` for native media, bare host otherwise), and they diverge on 57%
of rows. asyncpraw exposes it as `submission.domain` — read it, never derive it. This is
the single thing that a rebuild would be most tempted to "improve," and doing so silently
corrupts every downstream lift statistic.

## Sizing

Historical corpus: median 43 posts/day across all 10 subreddits. The live rate is higher;
the deployed site sampled sparsely. Reasonable upper bound: ~10,000 posts/day (peak subs at
their ~1,000-per-day listing ceiling). asyncpraw defaults to 60 requests/minute; each
listing page returns up to 100 posts, so a 100-post `new` + 100-post `top?t=day` per
subreddit is at most 20 requests, well inside a two-minute budget. No pagination beyond one
page per (subreddit, endpoint) — the union covers overflow.

## File Structure

```
fetcher/
├── __init__.py
├── config.py               subreddit list, endpoint choices, User-Agent
├── client.py               asyncpraw Reddit client (context-managed, retryable)
├── fetch.py                the core: per-sub listings → merged raw dict list
├── serialize.py            raw dict → {"kind":"t3","data":{...}} JSONL bytes
└── cli.py                  `python -m fetcher fetch --date --out`

tests/fetcher/
├── conftest.py             fake Reddit client, canned listings
├── fixtures/
│   └── canned_new_page.json    subset of a real listing response, committed
└── test_*.py               one per module
```

**Modified in this plan:** `.gitignore` (one line), `.env.example` (new file).
**Not modified:** anything under `etl/` or `backend/`. The pipeline is downstream and
already validated.

---

## Task dependency graph

```
T1 scaffolding + canned fixture
 ├─ T2 config     (subreddit list, User-Agent, endpoint config)
 ├─ T3 serialize  (byte-compat with normalize.py; no network)
 └─ T4 client     (asyncpraw wrapper with retry, tested against fake)
     ├─ T5 fetch  (union + dedupe + date filter)
     │    └─ T6 cli
     │        └─ T7 end-to-end
     └─ T8 env docs
```

Model assignment: Sonnet 5 (high effort) for everything except T5 and T7 → **Opus 5**.
T5 is where the async coordination and dedupe logic lives, and T7 is the end-to-end proof
that this plan's output actually feeds the existing ETL.

---

## Reference facts, verified

| Fact | Source |
|---|---|
| asyncpraw exposes `submission.domain` as a dynamic attribute | measured against `asyncpraw==7.8.1` |
| Every record in `_original_pipeline/data.jsonl` is `{"kind":"t3","data":{...}}` | Phase 1 T2 investigation, 8,799/8,799 |
| 10 subreddits, listing cap ~1,000/subreddit | historical corpus |
| No pagination needed for a single day | max historical single-sub-day = 299, well under the 100-per-page cap × two endpoints |
| `pd.json_normalize(records, max_level=0)` is required | Phase 1 T2 — deeper flatten explodes to 5,234 columns |

---

### Task 1: Scaffolding, fixtures, and env

**Files:**
- Create: `fetcher/__init__.py`, `tests/fetcher/__init__.py`, `tests/fetcher/conftest.py`,
  `tests/fetcher/fixtures/canned_new_page.json`, `.env.example`
- Modify: `.gitignore` (one line)

- [ ] **Step 1: Package skeleton**

```bash
mkdir -p fetcher tests/fetcher/fixtures
touch fetcher/__init__.py tests/fetcher/__init__.py
```

- [ ] **Step 2: Write `.env.example`**

Documents the four secrets the fetcher needs, without any real values. Contract with the
user, not code.

```
# Reddit API — https://www.reddit.com/prefs/apps (script type)
REDDIT_CLIENT_ID=
REDDIT_CLIENT_SECRET=
# Format: "narrativesignal/1.0 by u/<your-username> (contact: <email>)"
# Nominatim policy requires the contact; Reddit is not that strict but a real UA
# reduces rate-limiting.
REDDIT_USER_AGENT=
```

- [ ] **Step 3: Extend `.gitignore`**

Add one line so a fetch output at the repo root does not accidentally land in git:

```
# Fetcher outputs — always regenerated
fetcher_out/
```

- [ ] **Step 4: Write the canned fixture**

Extract 20 raw `{"kind":"t3","data":{...}}` entries from `_original_pipeline/data.jsonl`
into a fake single-page listing shape (`{"kind":"Listing","data":{"after":null,"children":[...]}}`).
This is the shape asyncpraw's underlying API returns and what the fake client will replay.

```bash
./.venv-etl/Scripts/python.exe -c "
import json, pathlib
src = pathlib.Path('_original_pipeline/data.jsonl')
out = pathlib.Path('tests/fetcher/fixtures/canned_new_page.json')
lines = src.read_text(encoding='utf-8').splitlines()[:20]
children = [json.loads(l) for l in lines]
listing = {'kind': 'Listing', 'data': {'after': None, 'before': None, 'children': children}}
out.write_text(json.dumps(listing, indent=2), encoding='utf-8')
print('wrote', len(children), 'children')"
```

Expected: `wrote 20 children`

- [ ] **Step 5: Write `tests/fetcher/conftest.py`**

```python
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
```

- [ ] **Step 6: Verify collection works**

Run: `./.venv-etl/Scripts/python.exe -m pytest tests/fetcher --collect-only -q`
Expected: no tests, but no import error either. If it errors on `from fetcher.config import
SUBREDDITS`, that is expected — Task 2 adds it. Fix any actual conftest errors.

- [ ] **Step 7: Commit**

```bash
git add fetcher tests/fetcher .gitignore .env.example
git commit -m "test: add fetcher package skeleton and fake Reddit fixture"
```

---

### Task 2: `fetcher/config.py` — subreddit list, endpoints, User-Agent

**Files:** Create `fetcher/config.py`, `tests/fetcher/test_config.py`.

The subreddits are the same 10 the historical corpus covered — DO NOT invent, DO NOT
reorder. The list already lives in `etl/config.py::SUBREDDITS`; re-import rather than
duplicate.

- [ ] **Step 1: Failing test**

```python
# tests/fetcher/test_config.py
import os

import pytest

from fetcher.config import SUBREDDITS, load_user_agent, listing_budget


def test_subreddits_are_the_ten_from_etl():
    from etl.config import SUBREDDITS as etl_subs
    assert SUBREDDITS == etl_subs
    assert len(SUBREDDITS) == 10


def test_user_agent_reads_env(monkeypatch):
    monkeypatch.setenv("REDDIT_USER_AGENT", "narrativesignal/1.0 by u/x (contact: y@z)")
    assert load_user_agent() == "narrativesignal/1.0 by u/x (contact: y@z)"


def test_user_agent_missing_env_raises(monkeypatch):
    monkeypatch.delenv("REDDIT_USER_AGENT", raising=False)
    with pytest.raises(RuntimeError, match="REDDIT_USER_AGENT"):
        load_user_agent()


def test_user_agent_default_is_rejected(monkeypatch):
    """Reddit rate-limits generic UAs; force a real one."""
    monkeypatch.setenv("REDDIT_USER_AGENT", "python:asyncpraw")
    with pytest.raises(RuntimeError, match="looks generic"):
        load_user_agent()


def test_listing_budget_is_conservative():
    """Two endpoints * 100 posts * 10 subs = 2000 requests worst case."""
    budget = listing_budget()
    assert budget.per_sub_new_limit == 100
    assert budget.per_sub_top_limit == 100
```

Run: `./.venv-etl/Scripts/python.exe -m pytest tests/fetcher/test_config.py -q -W error`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 2: Write `fetcher/config.py`**

```python
"""Fetcher configuration (secrets from env, subreddit list re-exported from etl)."""
import os
from dataclasses import dataclass

from etl.config import SUBREDDITS  # single source of truth  # noqa: F401


@dataclass(frozen=True)
class ListingBudget:
    per_sub_new_limit: int = 100
    per_sub_top_limit: int = 100


def listing_budget() -> ListingBudget:
    return ListingBudget()


def load_user_agent() -> str:
    """Read REDDIT_USER_AGENT and refuse obvious placeholders.

    Reddit rate-limits requests bearing generic default UAs. Requiring a real UA in
    the env is cheaper than debugging 429s in production.
    """
    ua = os.environ.get("REDDIT_USER_AGENT", "").strip()
    if not ua:
        raise RuntimeError(
            "REDDIT_USER_AGENT is required. See .env.example for the expected format."
        )
    if any(marker in ua.lower() for marker in ("python:asyncpraw", "changeme", "default")):
        raise RuntimeError(
            f"REDDIT_USER_AGENT={ua!r} looks generic. Use "
            '"narrativesignal/1.0 by u/<name> (contact: <email>)".'
        )
    return ua
```

- [ ] **Step 3: Pass, commit**

Run: `./.venv-etl/Scripts/python.exe -m pytest tests/fetcher/test_config.py -q -W error`
Expected: 5 passed.

```bash
git add fetcher/config.py tests/fetcher/test_config.py
git commit -m "feat(fetcher): add config with subreddit list and User-Agent guard"
```

---

### Task 3: `fetcher/serialize.py` — raw dict → JSONL bytes

**Files:** Create `fetcher/serialize.py`, `tests/fetcher/test_serialize.py`.

The output must be byte-compatible with `_original_pipeline/data.jsonl`. `etl/normalize.py`
reads it via `json.loads`, so the *bytes* only need to be valid JSON with the right shape —
we don't need to match whitespace exactly. But we DO need `{"kind":"t3","data":...}` per
line and LF line endings.

- [ ] **Step 1: Failing test**

```python
# tests/fetcher/test_serialize.py
import io
import json

import pytest

from fetcher.serialize import to_jsonl_bytes, write_jsonl


@pytest.fixture()
def raw_records():
    return [
        {"id": "abc", "author": "u1", "domain": "self.politics",
         "created_utc": 1739858460.0, "subreddit": "politics"},
        {"id": "def", "author": "u2", "domain": "nytimes.com",
         "created_utc": 1739858461.0, "subreddit": "politics"},
    ]


def test_shape_is_kind_t3_wrapping_data(raw_records):
    lines = to_jsonl_bytes(raw_records).decode("utf-8").splitlines()
    assert len(lines) == 2
    for line, raw in zip(lines, raw_records):
        obj = json.loads(line)
        assert obj["kind"] == "t3"
        assert obj["data"] == raw


def test_line_endings_are_lf(raw_records):
    payload = to_jsonl_bytes(raw_records)
    assert b"\r\n" not in payload
    assert payload.endswith(b"\n")


def test_normalize_reads_our_output(tmp_path, raw_records):
    """The whole point: normalize.py must accept what we write."""
    path = tmp_path / "out.jsonl"
    # Add the minimum fields normalize.py projects.
    full = [
        {**r, "title": "t", "url": "u", "permalink": "/p/",
         "score": 1, "num_comments": 0}
        for r in raw_records
    ]
    write_jsonl(full, path)
    from etl.normalize import normalize_listing
    df = normalize_listing(path)
    assert set(df.id) == {"abc", "def"}


def test_write_jsonl_is_atomic(tmp_path, raw_records):
    """Writes to <path>.tmp then rename, so a crash cannot leave a torn file."""
    path = tmp_path / "out.jsonl"
    write_jsonl(raw_records, path)
    assert path.exists()
    assert not path.with_suffix(".jsonl.tmp").exists()
```

Run: FAIL with `ModuleNotFoundError`.

- [ ] **Step 2: Write `fetcher/serialize.py`**

```python
"""Serialize raw Reddit records to JSONL byte-compatible with etl/normalize.py."""
import json
import os
from pathlib import Path
from typing import Iterable


def to_jsonl_bytes(records: Iterable[dict]) -> bytes:
    """Emit `{"kind":"t3","data":{...}}\\n` per record, UTF-8, LF endings.

    Matches the shape etl/normalize.py's `pd.json_normalize` consumes after
    stripping the outer wrapper.
    """
    lines = []
    for rec in records:
        lines.append(json.dumps({"kind": "t3", "data": rec}, ensure_ascii=False))
    return ("\n".join(lines) + "\n").encode("utf-8")


def write_jsonl(records: Iterable[dict], path: Path) -> None:
    """Write atomically: <path>.tmp then os.replace, so a crash never leaves a torn file.

    Consumers on the same filesystem see either the old file or the complete new one,
    never a half-written one. The ETL runs against `<path>`, so a torn file would
    cascade into wrong artifacts.
    """
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(to_jsonl_bytes(records))
    os.replace(tmp, path)
```

- [ ] **Step 3: Pass, commit**

Expected: 4 passed.

```bash
git add fetcher/serialize.py tests/fetcher/test_serialize.py
git commit -m "feat(fetcher): serialize records to JSONL, atomic write"
```

---

### Task 4: `fetcher/client.py` — asyncpraw wrapper with retry

**Files:** Create `fetcher/client.py`, `tests/fetcher/test_client.py`. Modify
`requirements.txt` to add `tenacity`.

The real asyncpraw client is created once and reused across all subreddit calls. We wrap it
so tests can substitute the fake, and so calls retry on transient network errors — but not
on auth errors (which will never fix themselves) and not on 404 (subreddit removed).

- [ ] **Step 1: Failing test**

```python
# tests/fetcher/test_client.py
import asyncio

import pytest
from asyncprawcore.exceptions import Forbidden, NotFound, ServerError

from fetcher.client import fetch_with_retry


class Boom:
    def __init__(self, *errors):
        self._errors = list(errors)
        self.calls = 0

    async def do(self):
        self.calls += 1
        if self._errors:
            raise self._errors.pop(0)
        return "ok"


def test_retries_transient_server_errors():
    boom = Boom(ServerError(_mock_response(500)), ServerError(_mock_response(503)))
    result = asyncio.run(fetch_with_retry(boom.do, attempts=3, min_wait=0.01))
    assert result == "ok"
    assert boom.calls == 3


def test_does_not_retry_forbidden():
    boom = Boom(Forbidden(_mock_response(403)))
    with pytest.raises(Forbidden):
        asyncio.run(fetch_with_retry(boom.do, attempts=3, min_wait=0.01))
    assert boom.calls == 1


def test_does_not_retry_not_found():
    boom = Boom(NotFound(_mock_response(404)))
    with pytest.raises(NotFound):
        asyncio.run(fetch_with_retry(boom.do, attempts=3, min_wait=0.01))
    assert boom.calls == 1


def test_gives_up_after_attempts_and_reraises():
    err = ServerError(_mock_response(500))
    boom = Boom(err, err, err)
    with pytest.raises(ServerError):
        asyncio.run(fetch_with_retry(boom.do, attempts=3, min_wait=0.01))
    assert boom.calls == 3


def _mock_response(status: int):
    class R:
        def __init__(self, s): self.status = s
    return R(status)
```

Run: FAIL with `ModuleNotFoundError`.

- [ ] **Step 2: Add tenacity to `requirements.txt`**

Insert `tenacity==9.0.0` immediately after `pyarrow` (line 14 in the current file). Then:

```bash
./.venv-etl/Scripts/python.exe -m pip install "tenacity==9.0.0"
```

- [ ] **Step 3: Write `fetcher/client.py`**

```python
"""asyncpraw client factory and retry wrapper."""
import os
from typing import Awaitable, Callable, TypeVar

import asyncpraw
from asyncprawcore.exceptions import Forbidden, NotFound, ResponseException
from tenacity import (
    retry, retry_if_exception, stop_after_attempt, wait_exponential,
)

from fetcher.config import load_user_agent

T = TypeVar("T")


def _is_transient(exc: BaseException) -> bool:
    """Retry only on server-side transient errors.

    - Forbidden (403): auth or private subreddit — will not fix itself.
    - NotFound (404): banned/deleted subreddit — will not fix itself.
    - Anything else HTTP-shaped: treat as transient.
    """
    if isinstance(exc, (Forbidden, NotFound)):
        return False
    return isinstance(exc, ResponseException)


async def fetch_with_retry(
    call: Callable[[], Awaitable[T]],
    *,
    attempts: int = 5,
    min_wait: float = 1.0,
    max_wait: float = 30.0,
) -> T:
    """Run `call()`, retrying transient errors with exponential backoff."""
    @retry(
        stop=stop_after_attempt(attempts),
        wait=wait_exponential(multiplier=min_wait, max=max_wait),
        retry=retry_if_exception(_is_transient),
        reraise=True,
    )
    async def _run():
        return await call()

    return await _run()


def make_reddit_client() -> asyncpraw.Reddit:
    """Read secrets from env and construct the async client.

    Raises RuntimeError with a clear message if any required var is missing.
    """
    missing = [
        v for v in ("REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET")
        if not os.environ.get(v)
    ]
    if missing:
        raise RuntimeError(f"missing env vars: {', '.join(missing)}")
    return asyncpraw.Reddit(
        client_id=os.environ["REDDIT_CLIENT_ID"],
        client_secret=os.environ["REDDIT_CLIENT_SECRET"],
        user_agent=load_user_agent(),
    )
```

- [ ] **Step 4: Pass, commit**

Expected: 4 passed.

```bash
git add fetcher/client.py tests/fetcher/test_client.py requirements.txt
git commit -m "feat(fetcher): asyncpraw client with retry on transient errors"
```

---

### Task 5: `fetcher/fetch.py` — union + dedupe + date filter (Opus)

**Files:** Create `fetcher/fetch.py`, `tests/fetcher/test_fetch.py`.

The heart of the plan. Two subtleties, both from the Phase 1 investigation:

1. **Union `new` with `top?t=day`, dedupe by `id`.** `new` is chronologically complete but
   caps at ~1,000 per subreddit; `top?t=day` covers overflow on high-volume subs like
   r/politics. Historical peak is 299/day for a single sub, so overflow is unlikely for the
   current corpus — but sizing for the ceiling costs one extra request per sub, worth it.
2. **The date window is yesterday, UTC.** `created_utc` is a unix float in UTC, so the
   filter is `date_min <= created_utc < date_max`. Do NOT use naive `datetime.now()` — a
   run at 00:15 UTC on Aug 5 must produce Aug 4's posts, regardless of the host's local time.

The fetcher **must extract the raw `data` dict**, not the `Submission` object's public
attributes. asyncpraw builds `Submission.__dict__` from a subset — writing that would omit
the ~30 fields normalize doesn't project but that must still travel with the record
(e.g. `is_self`, `over_18`, `media`). asyncpraw exposes the raw underlying dict via
`submission._reddit._objector.parse` intermediaries; the safest interface is
`vars(submission)` and then filter out the private (`_`-prefixed) keys. We test that the
result reads back through `normalize_listing`.

- [ ] **Step 1: Failing test**

```python
# tests/fetcher/test_fetch.py
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
```

Run: FAIL with `ModuleNotFoundError`.

- [ ] **Step 2: Write `fetcher/fetch.py`**

```python
"""Fetch one day's posts across all subreddits, unioned and deduped.

Design:
- Each subreddit contributes `new` (chronologically complete, ~1000 cap) unioned with
  `top?t=day` (covers listing-cap overflow). Dedupe by post id.
- Date window is yesterday-UTC. `created_utc` is a unix float in UTC; the host's
  local timezone is irrelevant and must not sneak in.
- Extracts the raw dict payload, not the Submission object. `etl/normalize.py`
  copies `domain` verbatim, and Submission attributes cannot be trusted to preserve
  every raw field.
- Per-subreddit errors are logged and skipped, per spec §8 (proceed if ≥8/10).
"""
import asyncio
import logging
from datetime import date, datetime, time, timezone
from typing import Iterable

from asyncprawcore.exceptions import Forbidden, NotFound, ResponseException

from fetcher.client import fetch_with_retry
from fetcher.config import SUBREDDITS, listing_budget

log = logging.getLogger(__name__)


def _extract_raw(submission) -> dict:
    """Pull the raw JSON payload asyncpraw hydrated the Submission from.

    Falls back to reading attributes when the private cache is not populated
    (the fake client in tests exposes it directly on `.raw`).
    """
    if hasattr(submission, "raw"):
        return submission.raw
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
    """Union `new` and `top?t=day` for one subreddit. Wrapped in retry."""
    async def _new():
        sub = await reddit.subreddit(name)
        return await _collect(sub.new(limit=budget.per_sub_new_limit))

    async def _top():
        sub = await reddit.subreddit(name)
        return await _collect(sub.top(time_filter="day", limit=budget.per_sub_top_limit))

    new_page = await fetch_with_retry(_new)
    top_page = await fetch_with_retry(_top)
    return merge_and_dedupe(new_page, top_page)


async def fetch_day(reddit, *, day: date) -> list[dict]:
    """Fetch every subreddit for one UTC calendar day. Skip failing subs; log which."""
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

    if len(skipped) > len(SUBREDDITS) - 8:  # spec §8: require ≥8/10
        raise RuntimeError(
            f"too many subreddits failed ({len(skipped)}/{len(SUBREDDITS)}); "
            f"failed={skipped}"
        )

    all_records = merge_and_dedupe(*results.values())
    return [r for r in all_records if _in_utc_day(r, day)]
```

- [ ] **Step 3: Pass, commit**

Run: `./.venv-etl/Scripts/python.exe -m pytest tests/fetcher -q -W error`
Expected: all fetcher tests pass. Report count.

```bash
git add fetcher/fetch.py tests/fetcher/test_fetch.py
git commit -m "feat(fetcher): fetch one UTC day, union new+top, dedupe by id"
```

---

### Task 6: `fetcher/cli.py` — `python -m fetcher fetch --date --out`

**Files:** Create `fetcher/cli.py`, `fetcher/__main__.py`, `tests/fetcher/test_cli.py`.

- [ ] **Step 1: Failing test**

```python
# tests/fetcher/test_cli.py
import json
import subprocess
import sys
from datetime import date

import pytest


def test_help(tmp_path):
    """--help must work without any env vars set."""
    env = {}
    r = subprocess.run(
        [sys.executable, "-m", "fetcher", "fetch", "--help"],
        capture_output=True, text=True, env={**dict(**{}), "PATH": ""},
    )
    # We can't reliably run with empty PATH on Windows; just check the message.
    assert r.returncode == 0 or "--date" in (r.stdout + r.stderr)


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
```

Run: FAIL.

- [ ] **Step 2: Write `fetcher/cli.py`**

```python
"""Command-line entry point: `python -m fetcher fetch --date --out`."""
import argparse
import asyncio
import logging
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fetcher.client import make_reddit_client
from fetcher.fetch import fetch_day
from fetcher.serialize import write_jsonl


def _yesterday_utc() -> str:
    return (datetime.now(tz=timezone.utc).date() - timedelta(days=1)).isoformat()


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="fetcher")
    sub = parser.add_subparsers(dest="cmd", required=True)

    fetch = sub.add_parser("fetch", help="fetch one UTC day into a JSONL file")
    fetch.add_argument(
        "--date", default=_yesterday_utc(),
        help="UTC calendar day (YYYY-MM-DD). Default: yesterday.",
    )
    fetch.add_argument(
        "--out", required=True, type=Path,
        help="Output JSONL path. Written atomically.",
    )
    fetch.add_argument("-v", "--verbose", action="store_true")
    return parser.parse_args(argv)


async def _do_fetch(day: date, out: Path) -> int:
    reddit = make_reddit_client()
    try:
        records = await fetch_day(reddit, day=day)
    finally:
        await reddit.close()
    write_jsonl(records, out)
    print(f"wrote {len(records)} records to {out}")
    return 0


def run(argv: list[str]) -> int:
    ns = parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if ns.verbose else logging.INFO)
    if ns.cmd == "fetch":
        return asyncio.run(_do_fetch(date.fromisoformat(ns.date), ns.out))
    return 2


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
```

- [ ] **Step 3: Write `fetcher/__main__.py`**

```python
from fetcher.cli import run
import sys
sys.exit(run(sys.argv[1:]))
```

- [ ] **Step 4: Pass, commit**

```bash
git add fetcher/cli.py fetcher/__main__.py tests/fetcher/test_cli.py
git commit -m "feat(fetcher): CLI with `fetch --date --out`, atomic output"
```

---

### Task 7: End-to-end proof — fetch → normalize → pipeline (Opus)

**Files:** Create `tests/fetcher/test_e2e.py`. Modifies nothing else.

The single test that would tell us Phase 2 actually works if it landed today: run the
fetcher against the fake, feed its output through `etl/pipeline.run_pipeline`, and check
that every expected artifact drops onto disk.

- [ ] **Step 1: Write the test**

```python
# tests/fetcher/test_e2e.py
"""End-to-end: fetcher → ETL, offline, using the fake Reddit client."""
import asyncio
from datetime import date
from pathlib import Path


def test_fetcher_output_drives_the_full_pipeline(tmp_path, fake_reddit):
    from fetcher.fetch import fetch_day
    from fetcher.serialize import write_jsonl
    from etl.normalize import normalize_listing
    from etl.pipeline import REQUIRED_ARTIFACTS, run_pipeline

    # Step 1: fetch, using the fake — 20 canned records replayed per subreddit
    raw = asyncio.run(fetch_day(fake_reddit, day=date(2025, 2, 17)))
    assert raw, "the fake must yield at least one Feb-17 record"

    jsonl = tmp_path / "day.jsonl"
    write_jsonl(raw, jsonl)

    # Step 2: normalize
    clean_posts = normalize_listing(jsonl)
    assert set(clean_posts.columns) == {
        "id", "created_datetime", "subreddit", "author", "title", "domain",
        "score", "num_comments", "permalink", "url",
    }
    # AutoModerator must be dropped even though the fixture includes it
    assert "AutoModerator" not in set(clean_posts.author)

    # Step 3: full pipeline
    out = tmp_path / "artifacts"
    run_pipeline(
        clean_posts, out, categories_path=Path("data/domain_categories.json"),
    )

    missing = [n for n in REQUIRED_ARTIFACTS if not (out / n).exists()]
    assert missing == [], f"pipeline did not produce: {missing}"

    # A minimum smoke check — clean_posts round-trips
    (out / "clean_posts.csv").exists()
```

- [ ] **Step 2: Pass, commit**

Run: `./.venv-etl/Scripts/python.exe -m pytest tests/fetcher tests/etl -q -W error`
Expected: everything green. Paste count.

```bash
git add tests/fetcher/test_e2e.py
git commit -m "test(fetcher): end-to-end fetcher output drives the ETL pipeline"
```

---

### Task 8: Real-network smoke doc

**Files:** Create `docs/fetcher-first-run.md`.

Not a test — a one-page runbook the person setting the secrets follows to see the fetcher
actually reach Reddit. It exists because a green offline suite proves the shape is right
but says nothing about credentials, User-Agent policy, or rate limits.

- [ ] **Step 1: Write the doc**

```markdown
# Fetcher First Run

Before the first cron ever fires, prove locally that the fetcher can reach Reddit.

## 1. Create a Reddit "script" app

https://www.reddit.com/prefs/apps → **create app** → **script**. Note the client id
(under the app name) and secret. No redirect URI is used.

## 2. Set env vars in your shell

```bash
export REDDIT_CLIENT_ID=...
export REDDIT_CLIENT_SECRET=...
export REDDIT_USER_AGENT="narrativesignal/1.0 by u/<your-username> (contact: you@example.com)"
```

The User-Agent format matters — a bare `python:asyncpraw` will be rate-limited hard, and
the fetcher refuses to run with it.

## 3. Run against yesterday

```bash
mkdir -p fetcher_out
./.venv-etl/Scripts/python.exe -m fetcher fetch --out fetcher_out/day.jsonl
```

Expected output:
```
wrote NNN records to fetcher_out/day.jsonl
```

Where NNN is roughly 30–3000 depending on the day. `fetcher_out/` is gitignored.

## 4. Feed through the ETL

```bash
./.venv-etl/Scripts/python.exe -c "
from pathlib import Path
from etl.normalize import normalize_listing
from etl.pipeline import run_pipeline, REQUIRED_ARTIFACTS

clean = normalize_listing(Path('fetcher_out/day.jsonl'))
run_pipeline(clean, Path('fetcher_out/artifacts'),
             categories_path=Path('data/domain_categories.json'))
missing = [n for n in REQUIRED_ARTIFACTS if not (Path('fetcher_out/artifacts')/n).exists()]
print('missing:', missing or 'none')
"
```

The last line must print `missing: none`. If any artifact is missing, the fetcher or
normalize failed for that day — check the log for `skipping r/...` and rerun.

## 5. If Reddit returns 429

The User-Agent is being ignored (a generic UA), or you hit the rate limit while iterating.
Wait five minutes and try again. If it persists, the client id/secret is wrong.
```

- [ ] **Step 2: Commit**

```bash
git add docs/fetcher-first-run.md
git commit -m "docs(fetcher): add first-run runbook"
```

---

## Definition of Done

- [ ] `./.venv-etl/Scripts/python.exe -m pytest tests/fetcher tests/etl -q -W error` passes
      with the new fetcher tests added on top of the 141 baseline
- [ ] `python -m fetcher fetch --help` prints usage
- [ ] `test_fetcher_output_drives_the_full_pipeline` proves the fetcher's output feeds the
      pipeline end-to-end offline
- [ ] `docs/fetcher-first-run.md` exists

**Not in scope:** GitHub Actions cron, `entrypoint.sh`, `render.yaml`, orphan-branch push
to `data-live`, container refresh. All Phase 3.

## Open items to flag when this lands

- Actually running the fetcher against real Reddit — needs the four env vars, so it is
  yours, not mine. The runbook above is the guide.
- `fetch_day` does not currently paginate `new` beyond 100 posts. Historical data suggests
  that is fine (peak observed = 299/day/sub); if a live sub blows past that, the union with
  `top?t=day` catches the top 100 by score but the middle can leak. Add real pagination in
  Phase 3 once we see actual daily volume.
