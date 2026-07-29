"""Tests for `enrich.categorize_domains`.

The real `enrich.llm.classify` is never called here; a callable is injected via
the `llm=` kwarg. `data/domain_categories.json` is never touched; a tmp copy
is used.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from enrich.categorize_domains import (
    CATEGORIES,
    PROMPT,
    _reseed_defaults,
    categorize,
)
from enrich.llm import ClassifyError
from etl.categories import CategoryStore


@pytest.fixture
def store(tmp_path: Path) -> CategoryStore:
    """Empty CategoryStore backed by a scratch file — never touches repo data."""
    return CategoryStore(tmp_path / "domain_categories.json")


# ---------------------------------------------------------------------------
# Golden path
# ---------------------------------------------------------------------------


def test_categorize_learns_every_fresh_domain(store: CategoryStore) -> None:
    """5 fresh domains -> 5 calls, store is fully populated, pending drains."""
    answers = {
        "example.com": "News",
        "opinion.blog": "Blogs",
        "epa.gov": "Government",
        "orgwatch.org": "Advocacy",
        "clips.tv": "Video",
    }
    calls: list[str] = []

    def fake(prompt: str, choices: list[str]) -> str:
        assert choices == CATEGORIES  # exact vocab is contract
        for dom, label in answers.items():
            if f'"{dom}"' in prompt:
                calls.append(dom)
                return label
        raise AssertionError(f"unexpected prompt: {prompt!r}")

    asked, skipped = categorize(store, list(answers), llm=fake)

    assert asked == 5
    assert skipped == 0
    assert sorted(calls) == sorted(answers)
    for dom, label in answers.items():
        assert store.category_of(dom) == label
    # Every fresh domain was learned; nothing left dangling in pending.
    assert store.pending == set()


# ---------------------------------------------------------------------------
# Cache-first behavior
# ---------------------------------------------------------------------------


def test_categorize_skips_domains_already_in_store(store: CategoryStore) -> None:
    """Domains already learned must not trigger a classify() call."""
    store.learn(
        {"cached-a.com": "News", "cached-b.org": "Advocacy", "cached-c.gov": "Government"}
    )
    call_count = 0

    def fake(prompt: str, choices: list[str]) -> str:
        nonlocal call_count
        call_count += 1
        if '"fresh-d.tv"' in prompt:
            return "Video"
        if '"fresh-e.io"' in prompt:
            return "Blogs"
        raise AssertionError(f"unexpected prompt: {prompt!r}")

    worklist = [
        "cached-a.com",
        "cached-b.org",
        "cached-c.gov",
        "fresh-d.tv",
        "fresh-e.io",
    ]
    asked, skipped = categorize(store, worklist, llm=fake)

    assert asked == 2
    assert call_count == 2
    assert skipped == 0
    assert store.category_of("fresh-d.tv") == "Video"
    assert store.category_of("fresh-e.io") == "Blogs"
    # Cached entries are unchanged.
    assert store.category_of("cached-a.com") == "News"


# ---------------------------------------------------------------------------
# Cap enforcement
# ---------------------------------------------------------------------------


def test_categorize_caps_calls_per_invocation(store: CategoryStore) -> None:
    """A worklist larger than `cap` is truncated; the extras are deferred."""
    worklist = [f"dom{i}.com" for i in range(100)]
    call_count = 0

    def fake(prompt: str, choices: list[str]) -> str:
        nonlocal call_count
        call_count += 1
        return "Other"

    asked, skipped = categorize(store, worklist, cap=10, llm=fake)

    assert asked == 10
    assert skipped == 90
    assert call_count == 10
    learned = sum(1 for d in worklist if store.category_of(d) == "Other")
    assert learned == 10


# ---------------------------------------------------------------------------
# Per-domain failure isolation
# ---------------------------------------------------------------------------


def test_categorize_isolates_classify_error_to_one_domain(
    store: CategoryStore, caplog: pytest.LogCaptureFixture
) -> None:
    """A ClassifyError on one domain must not stop subsequent ones."""
    def fake(prompt: str, choices: list[str]) -> str:
        if '"bad.example"' in prompt:
            raise ClassifyError("LLM returned out-of-vocab: 'banana'")
        return "News"

    with caplog.at_level("WARNING", logger="enrich.categorize_domains"):
        asked, skipped = categorize(
            store,
            ["bad.example", "good.example", "also.example"],
            llm=fake,
        )

    # All three attempts counted; only the two successes were learned.
    assert asked == 3
    assert skipped == 0
    assert store.category_of("bad.example") is None
    assert store.category_of("good.example") == "News"
    assert store.category_of("also.example") == "News"
    # The failure was surfaced via logging, not swallowed silently.
    assert any("bad.example" in r.message for r in caplog.records)


def test_categorize_ignores_out_of_vocab_return_from_injected_llm(
    store: CategoryStore,
) -> None:
    """Defensive: if a fake `llm` returns junk, don't pollute the store."""
    def fake(prompt: str, choices: list[str]) -> str:
        if '"weird.example"' in prompt:
            return "TotallyMadeUp"
        return "News"

    asked, skipped = categorize(store, ["weird.example", "ok.example"], llm=fake)

    assert asked == 2
    assert skipped == 0
    assert store.category_of("weird.example") is None
    assert store.category_of("ok.example") == "News"


# ---------------------------------------------------------------------------
# Legacy-label migration
# ---------------------------------------------------------------------------


def test_reseed_defaults_rewrites_legacy_labels(tmp_path: Path) -> None:
    """`Media / News` and `Advocacy / Org` migrate; current-vocab entries untouched."""
    path = tmp_path / "domain_categories.json"
    path.write_text(
        json.dumps(
            {
                "cnn.example": "Media / News",
                "advocate.example": "Advocacy / Org",
                "gov.example": "Government",
                "tube.example": "Video",
                "already.example": "News",
            }
        ),
        encoding="utf-8",
    )
    store = CategoryStore(path)

    rewritten = _reseed_defaults(store)

    assert rewritten == 2
    assert store.category_of("cnn.example") == "News"
    assert store.category_of("advocate.example") == "Advocacy"
    assert store.category_of("gov.example") == "Government"
    assert store.category_of("tube.example") == "Video"
    assert store.category_of("already.example") == "News"


def test_reseed_defaults_is_idempotent(tmp_path: Path) -> None:
    """Running the migration twice must not double-count or corrupt anything."""
    path = tmp_path / "domain_categories.json"
    path.write_text(
        json.dumps({"cnn.example": "Media / News"}), encoding="utf-8"
    )
    store = CategoryStore(path)

    first = _reseed_defaults(store)
    second = _reseed_defaults(store)

    assert first == 1
    assert second == 0
    assert store.category_of("cnn.example") == "News"


# ---------------------------------------------------------------------------
# Prompt hygiene
# ---------------------------------------------------------------------------


def test_prompt_template_lists_exact_vocab() -> None:
    """Guard against silent drift between the prompt copy and CATEGORIES."""
    for label in CATEGORIES:
        assert label in PROMPT
