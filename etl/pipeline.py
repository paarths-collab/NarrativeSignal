"""Pipeline orchestrator: clean_posts -> every derived artifact (spec §7).

One entry point, `run_pipeline`, wires the transforms together in dependency order
and writes each result to `out_dir` as CSV.

THE ROW-COUNT CASCADE
---------------------
Three different frames flow through here and they are NOT interchangeable::

    clean_posts   8,687   raw minus author == "AutoModerator"
    clusters      8,567   clean_posts minus author == "[deleted]", + cluster column
    posts         8,567   clusters without the cluster column

* `build_ideological_matrix` reads **clean_posts**. The 10 domains contributed by
  [deleted] authors change every value; the 8,567 set reproduces only 10 of 45 rows.
* `build_clusters_table` also reads **clean_posts** — it clusters first and drops
  [deleted] afterwards, which is why a cluster can be a downstream singleton.
* Everything else that counts posts (`build_daily_volume`, `build_domain_flow`,
  `build_bridge_authors`, `build_distinctive_domains`, `build_influence_profile`)
  reads **posts**.

Swapping a level does not raise — it produces plausible numbers that are wrong.

NOT BYTE-REPRODUCIBLE
---------------------
`clean_top_distinctive_domains` and `echo_chamber_scores` depend on the media-universe
allowlist, and the original hand-curated list is lost. The seed at
`data/domain_categories.json` carries 41 of the 909 domains, so both artifacts differ
from the committed files by design. Every unresolved domain is recorded in
`uncategorized_domains.txt` as the worklist for a later categorization pass; this
module never calls an LLM.

`narrative_topic_mapping` labels are `LABEL_ME_*` placeholders — see
`build_topic_mapping`.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from etl.categories import CategoryStore
from etl.transforms.authors import (
    build_amplification_events,
    build_amplification_summary,
    build_bridge_authors,
    build_diffusion_table,
    build_influence_profile,
)
from etl.transforms.chatbot import (
    build_narrative_registry,
    build_subreddit_intelligence,
    build_user_intelligence,
)
from etl.transforms.dedupe import build_clusters_table
from etl.transforms.ecosystem import (
    build_distinctive_domains,
    build_domain_flow,
    build_echo_chambers,
)
from etl.transforms.ideological import build_ideological_matrix
from etl.transforms.narratives import (
    build_graph_edges,
    build_narrative_summary,
    build_spread_chains,
    build_topic_mapping,
)
from etl.transforms.volume import build_daily_volume

# Every CSV the pipeline must emit. Derived from the union of the two consumer
# manifests -- backend/patch_db.py TABLES (11 CSVs) and
# backend/hybrid_chatbot/config.py CSV_TABLES (12 CSVs), which overlap in 7 for a
# union of 16 -- plus the three artifacts no consumer maps today but every
# transform still produces: amplification_event_table (the input
# author_amplification_summary is aggregated from), clean_top_distinctive_domains
# (the input echo_chamber_scores is aggregated from) and user_intelligence_summary
# (superseded by author_influence_profile, still shipped). 19 files, which is
# exactly the set of CSVs sitting in data/. tests/etl/test_pipeline.py asserts the
# two manifests stay covered.
REQUIRED_ARTIFACTS: list[str] = [
    "clean_posts.csv",
    "clean_with_clusters_v2.csv",
    "graph_edge_intelligence_table.csv",
    "narrative_intelligence_summary.csv",
    "narrative_spread_chain_table.csv",
    "narrative_topic_mapping.csv",
    "narrative_registry.csv",
    "narrative_diffusion_table.csv",
    "amplification_event_table.csv",
    "author_amplification_summary.csv",
    "author_influence_profile.csv",
    "bridge_authors_v2.csv",
    "user_intelligence_summary.csv",
    "subreddit_intelligence_summary.csv",
    "subreddit_domain_flow_v2.csv",
    "clean_top_distinctive_domains.csv",
    "echo_chamber_scores.csv",
    "daily_volume_v2.csv",
    "ideological_distance_matrix.csv",
]

# Worklist for the categorization pass. Not an artifact: no consumer reads it and
# it is not a CSV.
UNCATEGORIZED_DOMAINS_FILE = "uncategorized_domains.txt"


def _write(df: pd.DataFrame, path: Path) -> None:
    """CSV write contract: no index, LF endings on every platform.

    Without `lineterminator` pandas follows the platform default, so a Windows run
    would emit CRLF and change every byte of every artifact relative to a Linux run.
    """
    df.to_csv(path, index=False, lineterminator="\n")


def _record_pending_domains(store: CategoryStore, clean_posts: pd.DataFrame) -> None:
    """Probe every domain so unresolved ones land in `store.pending`.

    `build_distinctive_domains` takes the plain allowlist dict, not the store, so
    nothing else in the run touches `category_of` and `pending` would stay empty.
    Probing the 8,687 set (not the 8,567 one) keeps the worklist a superset: the
    10 domains only [deleted] authors posted still need a category eventually.
    """
    for domain in clean_posts["domain"].dropna().unique():
        store.category_of(domain)


def run_pipeline(
    clean_posts: pd.DataFrame,
    out_dir: Path,
    categories_path: Path,
) -> dict[str, pd.DataFrame]:
    """Build and write every artifact. Returns {filename: frame}.

    `clean_posts` is treated as read-only — callers share it with other consumers.
    `out_dir` is created if absent. `categories_path` points at the domain
    allowlist JSON; a missing file yields an empty allowlist rather than an error,
    so a first run still produces the worklist that fills it.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    store = CategoryStore(Path(categories_path))
    _record_pending_domains(store, clean_posts)

    # ── Cascade ──────────────────────────────────────────────────────────────
    clusters = build_clusters_table(clean_posts)          # 8,567 + cluster column
    posts = clusters.drop(columns=["duplicate_cluster_id"])

    # ── Narratives (all off the 285-row edge table) ──────────────────────────
    edges = build_graph_edges(clusters)
    summary = build_narrative_summary(edges)
    chains = build_spread_chains(edges)
    topics = build_topic_mapping(edges)
    registry = build_narrative_registry(edges)

    # ── Authors ──────────────────────────────────────────────────────────────
    diffusion = build_diffusion_table(edges)
    events = build_amplification_events(diffusion, edges)
    amplification_summary = build_amplification_summary(events)
    bridges = build_bridge_authors(posts)

    # Distances come from clean_posts; the influence profile's author universe
    # comes from the diffusion table, its post counts and subreddit spans from
    # `posts`.
    distances = build_ideological_matrix(clean_posts)
    influence = build_influence_profile(posts, diffusion, events, distances)

    # ── Media ecosystem ──────────────────────────────────────────────────────
    flow = build_domain_flow(posts)
    distinctive = build_distinctive_domains(posts, store.mapping)
    echo_chambers = build_echo_chambers(distinctive)

    # ── Chatbot rollups (echo_chambers is injected, never re-derived) ─────────
    subreddit_intel = build_subreddit_intelligence(edges, echo_chambers)
    user_intel = build_user_intelligence(edges, influence, amplification_summary)

    # ── Volume ───────────────────────────────────────────────────────────────
    daily_volume = build_daily_volume(posts)

    artifacts: dict[str, pd.DataFrame] = {
        "clean_posts.csv": clean_posts,
        "clean_with_clusters_v2.csv": clusters,
        "graph_edge_intelligence_table.csv": edges,
        "narrative_intelligence_summary.csv": summary,
        "narrative_spread_chain_table.csv": chains,
        "narrative_topic_mapping.csv": topics,
        "narrative_registry.csv": registry,
        "narrative_diffusion_table.csv": diffusion,
        "amplification_event_table.csv": events,
        "author_amplification_summary.csv": amplification_summary,
        "author_influence_profile.csv": influence,
        "bridge_authors_v2.csv": bridges,
        "user_intelligence_summary.csv": user_intel,
        "subreddit_intelligence_summary.csv": subreddit_intel,
        "subreddit_domain_flow_v2.csv": flow,
        "clean_top_distinctive_domains.csv": distinctive,
        "echo_chamber_scores.csv": echo_chambers,
        "daily_volume_v2.csv": daily_volume,
        "ideological_distance_matrix.csv": distances,
    }

    # Raised, not asserted: `python -O` strips asserts, and a silently missing
    # artifact surfaces as an empty table in the backend instead of a failed run.
    missing = [name for name in REQUIRED_ARTIFACTS if name not in artifacts]
    extra = [name for name in artifacts if name not in REQUIRED_ARTIFACTS]
    if missing or extra:
        raise ValueError(
            f"artifact set drifted from REQUIRED_ARTIFACTS: missing={missing}, "
            f"unexpected={extra}"
        )

    for name in REQUIRED_ARTIFACTS:
        _write(artifacts[name], out_dir / name)

    if store.pending:
        (out_dir / UNCATEGORIZED_DOMAINS_FILE).write_text(
            "\n".join(sorted(store.pending)) + "\n", encoding="utf-8"
        )

    return artifacts
