"""Categorize new domains against the fixed vocab via `enrich.llm.classify`.

Reads pending (uncategorized) domains from a `CategoryStore`, asks the LLM for
one of the seven allowed labels per domain, and merges the answers back into
the store. Cap-limits total LLM calls per invocation to protect the quota.

Vocabulary matches ``frontend/app/polar/PolarizeTreemap.jsx`` (`News`, `Blogs`,
`Advocacy`, `Research`, `Government`, `Video`, `Other`) so no frontend change
is needed. `_reseed_defaults()` is a one-time migration that rewrites the
41-domain seed's legacy labels (`Media / News`, `Advocacy / Org`) to that
vocab.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Iterable

from enrich.llm import ClassifyError, classify as _default_classify
from etl.categories import CategoryStore

LOG = logging.getLogger(__name__)

CATEGORIES: list[str] = [
    "News",
    "Blogs",
    "Advocacy",
    "Research",
    "Government",
    "Video",
    "Other",
]

# Legacy category labels used in the original hand-curated seed. Rewritten to
# the current vocab exactly once per store on first migration.
_LEGACY_MIGRATION: dict[str, str] = {
    "Media / News": "News",
    "Advocacy / Org": "Advocacy",
}

PROMPT: str = (
    'Given the news/media domain "{domain}", classify it as ONE of:\n'
    "News, Blogs, Advocacy, Research, Government, Video, Other.\n"
    "\n"
    "Answer with just the single word. If unsure between News and Blogs, "
    "prefer\nNews for established outlets and Blogs for personal/opinion "
    "sites."
)


def _reseed_defaults(store: CategoryStore) -> int:
    """Rewrite legacy category labels in `store` to the current vocab.

    One-time migration for the 41-domain seed shipped in
    ``data/domain_categories.json``. Returns the count of entries rewritten;
    idempotent (returns 0 after first successful call).
    """
    updates: dict[str, str] = {}
    for domain, label in store.mapping.items():
        replacement = _LEGACY_MIGRATION.get(label)
        if replacement is not None and replacement != label:
            updates[domain] = replacement
    if updates:
        store.learn(updates)
    return len(updates)


def categorize(
    store: CategoryStore,
    worklist: Iterable[str],
    *,
    batch: int = 8,
    cap: int = 900,
    llm: Callable[[str, list[str]], str] | None = None,
) -> tuple[int, int]:
    """Categorize new domains from `worklist`; return ``(asked, skipped)``.

    Skips any domain already known to `store`. Fires up to `batch` concurrent
    `classify()` calls via a thread pool; results are consumed in submission
    order so the effect on `store` is deterministic. Caps total calls at
    `cap` per invocation and logs the leftovers.

    A per-domain `ClassifyError` is caught and logged — the failing domain
    is not learned, and subsequent domains continue to be processed.

    Args:
        store: mutable cache. `store.learn({...})` is called for each
            successful classification.
        worklist: iterable of candidate domains. Duplicates are collapsed.
        batch: max concurrent LLM calls; 1 is effectively serial.
        cap: hard ceiling on LLM calls per invocation.
        llm: injectable classifier for tests. Defaults to
            :func:`enrich.llm.classify`.

    Returns:
        Two-tuple of ``(asked, skipped)``: `asked` counts LLM calls made
        (including failures); `skipped` counts domains beyond `cap` that
        were deferred.
    """
    call = llm if llm is not None else _default_classify

    fresh: list[str] = []
    seen: set[str] = set()
    for domain in worklist:
        if domain in seen:
            continue
        seen.add(domain)
        if store.category_of(domain) is not None:
            continue
        fresh.append(domain)

    to_ask = fresh[:cap]
    skipped = len(fresh) - len(to_ask)
    if skipped > 0:
        LOG.warning(
            "cap=%d reached; deferring %d domains to a later run", cap, skipped
        )

    if not to_ask:
        return 0, skipped

    asked = 0
    workers = max(1, min(batch, len(to_ask)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(call, PROMPT.format(domain=d), list(CATEGORIES))
            for d in to_ask
        ]
        # Consume in submission order for deterministic store mutation and
        # log ordering — parallelism still applies because all futures were
        # submitted before we started collecting results.
        for domain, fut in zip(to_ask, futures):
            asked += 1
            try:
                label = fut.result()
            except ClassifyError as exc:
                LOG.warning("classify failed for %s: %s", domain, exc)
                continue
            if label not in CATEGORIES:
                # `enrich.llm.classify` already normalizes to the allowed
                # set, but we defend against injected fakes and future drift.
                LOG.warning(
                    "out-of-vocab label %r for %s; not learning",
                    label,
                    domain,
                )
                continue
            store.learn({domain: label})

    return asked, skipped
