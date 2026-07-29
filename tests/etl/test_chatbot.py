# tests/etl/test_chatbot.py
"""The three chatbot-facing artifacts.

All three are being regenerated in the current integer narrative-id format. Two of
them turned out to be *fully* reconcilable against the committed Gen-A files, so the
reconciliation tests below are exact, not structural:

* narrative_registry -- the whole 1,055-row Gen-A file is reproduced row for row, in
  file order, by running build_narrative_registry over a frame whose unclustered
  posts fall back to a ``domain_domain`` id. The 154 clustered rows are identical to
  the regenerated registry once ``.0_`` is rewritten to ``_``.
* subreddit_intelligence_summary -- reproduced exactly, all 9 rows and all 7 columns.
  It was never Gen-A-id-shaped: its numbers already come from the 285-row edge table.
* user_intelligence_summary -- the seven duplicate-post columns reproduce 192/192.
  The five influence columns deliberately do NOT: Gen-A recomputed them locally with
  its own formula, which is the disagreement backend/hybrid/agents.py:83 warns about.
"""
import ast

import numpy as np
import pandas as pd
import pytest

from etl.config import DATA_DIR
from etl.transforms.authors import (
    build_amplification_events, build_amplification_summary,
    build_diffusion_table, build_influence_profile,
)
from etl.transforms.chatbot import (
    REGISTRY_COLUMNS, SUBREDDIT_INTEL_COLUMNS, USER_INTEL_COLUMNS,
    build_narrative_registry, build_subreddit_intelligence,
    build_user_intelligence,
)
from etl.transforms.dedupe import build_clusters_table
from etl.transforms.narratives import build_graph_edges, build_narrative_summary


@pytest.fixture(scope="module")
def edges(golden_clean_posts):
    return build_graph_edges(build_clusters_table(golden_clean_posts))


@pytest.fixture(scope="module")
def influence(golden_posts, edges, sample_distances):
    diffusion = build_diffusion_table(edges)
    events = build_amplification_events(diffusion, edges)
    return build_influence_profile(golden_posts, diffusion, events, sample_distances)


@pytest.fixture(scope="module")
def amplification_summary(edges):
    diffusion = build_diffusion_table(edges)
    return build_amplification_summary(build_amplification_events(diffusion, edges))


@pytest.fixture(scope="module")
def echo_chambers():
    return pd.read_csv(DATA_DIR / "echo_chamber_scores.csv")


@pytest.fixture(scope="module")
def committed_registry():
    return pd.read_csv(DATA_DIR / "narrative_registry.csv")


@pytest.fixture(scope="module")
def committed_subreddit_intel():
    return pd.read_csv(DATA_DIR / "subreddit_intelligence_summary.csv")


@pytest.fixture(scope="module")
def committed_user_intel():
    return pd.read_csv(DATA_DIR / "user_intelligence_summary.csv")


def _as_text(series: pd.Series) -> pd.Series:
    """Datetime -> the second-precision text the committed CSVs carry."""
    return series.dt.strftime("%Y-%m-%d %H:%M:%S")


# --------------------------------------------------------------- registry

def test_registry_shape_and_columns(edges):
    got = build_narrative_registry(edges)
    assert list(got.columns) == REGISTRY_COLUMNS
    assert REGISTRY_COLUMNS == [
        "narrative_id", "cluster_id", "primary_domain", "representative_title",
        "total_posts", "unique_subreddits", "first_seen",
    ]
    assert len(got) == 154
    assert got.narrative_id.is_unique


def test_registry_carries_no_float_ids(edges):
    """Gen-A wrote `0.0_salon.com`; the current format is `0_salon.com`."""
    got = build_narrative_registry(edges)
    assert not got.narrative_id.str.contains(r"\.0_", regex=True).any()
    assert not got.cluster_id.astype(str).str.contains(r"\.", regex=True).any()
    assert "0_salon.com" in set(got.narrative_id)


def test_registry_joins_the_narrative_summary(edges, committed_registry):
    """The whole point of regenerating: Gen-A joined 0 rows, this joins all 154."""
    summary = build_narrative_summary(edges)
    got = build_narrative_registry(edges)

    joined = got.merge(summary, on="narrative_id", how="inner")
    assert len(joined) == 154
    assert set(got.narrative_id) == set(summary.narrative_id)

    stale = set(committed_registry.narrative_id) & set(summary.narrative_id)
    assert stale == set(), "Gen-A ids were supposed to have zero overlap"


def test_registry_agrees_with_the_narrative_summary(edges):
    """Shared columns must not drift from narrative_intelligence_summary."""
    summary = build_narrative_summary(edges).set_index("narrative_id")
    got = build_narrative_registry(edges).set_index("narrative_id")
    assert (got.cluster_id == summary.cluster_id).all()
    assert (got.primary_domain == summary.primary_domain).all()
    assert (got.total_posts == summary.total_posts_x).all()
    assert (got.unique_subreddits == summary.unique_subreddits_x).all()
    assert (got.first_seen == summary.first_seen_x).all()


def test_registry_reconciles_with_gen_a_clustered_rows(edges, committed_registry):
    """154 of Gen-A's 1,055 rows are the clustered narratives -- and they match.

    Every column, not just the ids: this is what proves the regeneration changed
    the id format and nothing else.
    """
    gen_a = committed_registry[
        committed_registry.cluster_id.astype(str).str.fullmatch(r"\d+\.0")
    ].copy()
    assert len(gen_a) == 154
    gen_a["narrative_id"] = gen_a.narrative_id.str.replace(".0_", "_", regex=False)
    gen_a["cluster_id"] = gen_a.cluster_id.astype(float).astype(int)
    gen_a = gen_a.sort_values("narrative_id").reset_index(drop=True)

    got = build_narrative_registry(edges).sort_values("narrative_id").reset_index(
        drop=True
    )
    assert got.narrative_id.equals(gen_a.narrative_id)
    for col in ("cluster_id", "primary_domain", "representative_title",
                "total_posts", "unique_subreddits"):
        assert got[col].equals(gen_a[col]), f"{col} diverged from Gen-A"
    assert _as_text(got.first_seen).equals(gen_a.first_seen)


def test_registry_reproduces_the_full_gen_a_file(golden_clean_posts,
                                                 committed_registry):
    """The strong reconciliation: all 1,055 Gen-A rows, in file order.

    Gen-A ran the same aggregation over *all* 8,687 clean posts, mapping the
    cluster ids from the 8,567 set and falling back to `domain_domain` for the
    901 unclustered buckets. Reproducing it pins representative_title to the
    first post in file order -- NOT the highest-scoring one, which is what
    build_narrative_summary uses and which fits only 816 of 1,055 rows.
    """
    clusters = build_clusters_table(golden_clean_posts)
    ids = clusters.set_index("id")["duplicate_cluster_id"]

    frame = golden_clean_posts.copy()
    cluster_id = frame["id"].map(ids)
    frame["cluster_id"] = np.where(
        cluster_id.notna(), cluster_id.astype(str), frame["domain"].astype(str)
    )
    frame["narrative_id"] = frame["cluster_id"] + "_" + frame["domain"].astype(str)

    got = build_narrative_registry(frame)
    assert len(got) == len(committed_registry) == 1055
    assert got.narrative_id.equals(committed_registry.narrative_id)
    for col in ("cluster_id", "representative_title", "total_posts",
                "unique_subreddits"):
        assert got[col].astype(str).equals(committed_registry[col].astype(str)), col
    # One bucket -- `nan_nan` -- is all-null domain; None and NaN both blank out.
    assert got.primary_domain.fillna("").equals(
        committed_registry.primary_domain.fillna("")
    )
    assert _as_text(got.first_seen).equals(committed_registry.first_seen)


# ------------------------------------------------------ subreddit summary

def test_subreddit_intel_shape_and_columns(edges, echo_chambers):
    got = build_subreddit_intelligence(edges, echo_chambers)
    assert list(got.columns) == SUBREDDIT_INTEL_COLUMNS
    assert SUBREDDIT_INTEL_COLUMNS == [
        "subreddit", "total_duplicate_posts", "unique_narratives", "unique_users",
        "avg_score", "domain", "lift",
    ]
    assert len(got) == 9
    assert got.total_duplicate_posts.sum() == len(edges) == 285


def test_subreddit_intel_domain_is_a_stringified_list(edges, echo_chambers):
    """Same str(list) hazard as spread_sequence -- it must round-trip."""
    got = build_subreddit_intelligence(edges, echo_chambers)
    for value in got.domain:
        assert isinstance(value, str)
        parsed = ast.literal_eval(value)
        assert isinstance(parsed, list)
        assert 1 <= len(parsed) <= 3
        assert all(isinstance(d, str) for d in parsed)
    assert ast.literal_eval(
        got.set_index("subreddit").loc["worldpolitics", "domain"]
    ) == ["i.redd.it"]


def test_subreddit_intel_lift_comes_from_echo_chambers(edges, echo_chambers):
    got = build_subreddit_intelligence(edges, echo_chambers)
    expected = got.subreddit.map(echo_chambers.set_index("subreddit").lift)
    assert np.allclose(got.lift, expected, atol=1e-12)


def test_subreddit_intel_reconciles_with_the_committed_file(
    edges, echo_chambers, committed_subreddit_intel
):
    """Fully reproducible: 9 rows, 7 columns, same order."""
    got = build_subreddit_intelligence(edges, echo_chambers)
    exp = committed_subreddit_intel
    assert got.subreddit.equals(exp.subreddit)
    for col in ("total_duplicate_posts", "unique_narratives", "unique_users",
                "domain"):
        assert got[col].equals(exp[col]), f"{col} diverged from the committed file"
    assert np.allclose(got.avg_score, exp.avg_score, atol=1e-9)
    assert np.allclose(got.lift, exp.lift, atol=1e-9)


# ----------------------------------------------------------- user summary

def test_user_intel_shape_and_columns(edges, influence, amplification_summary):
    got = build_user_intelligence(edges, influence, amplification_summary)
    assert list(got.columns) == USER_INTEL_COLUMNS
    assert USER_INTEL_COLUMNS == [
        "author", "total_duplicate_posts", "unique_narratives",
        "communities_active_in", "first_seen", "last_seen", "most_common_domain",
        "total_posts", "narratives_transported",
        "avg_ideological_distance_crossed", "total_relative_amplification",
        "final_influence_score",
    ]
    assert len(got) == 192
    assert got.author.is_unique
    assert not got.isna().any().any()


def test_user_intel_takes_influence_columns_from_the_profile(
    edges, influence, amplification_summary
):
    """author_influence_profile is the single source of truth (agents.py:83)."""
    got = build_user_intelligence(edges, influence, amplification_summary)
    profile = influence.set_index("author")
    for col in ("total_posts", "narratives_transported",
                "avg_ideological_distance_crossed", "final_influence_score"):
        assert np.allclose(got[col], got.author.map(profile[col]), atol=1e-12), col

    # Not merely a rename of the duplicate-post stats: the profile counts every
    # post an author made, which is strictly more than their clustered ones.
    assert (got.total_posts >= got.total_duplicate_posts).all()
    assert (got.total_posts > got.total_duplicate_posts).any()


def test_user_intel_amplification_is_consistent_with_the_score(
    edges, influence, amplification_summary
):
    """total_relative_amplification must be the term the score was built from."""
    got = build_user_intelligence(edges, influence, amplification_summary)
    profile = influence.set_index("author")
    assert np.allclose(
        got.total_relative_amplification,
        got.author.map(profile.total_amplification_generated),
        atol=1e-9,
    )
    expected = (
        got.total_relative_amplification / 100.0
        + got.narratives_transported * 2
        + got.avg_ideological_distance_crossed * 5
    )
    assert np.allclose(got.final_influence_score, expected, atol=1e-9)


def test_user_intel_duplicate_stats_reconcile_with_gen_a(
    edges, influence, amplification_summary, committed_user_intel
):
    """Gen-A's duplicate-post half was already built on today's 285 edges: 192/192."""
    got = build_user_intelligence(edges, influence, amplification_summary)
    exp = committed_user_intel
    assert got.author.equals(exp.author)
    for col in ("total_duplicate_posts", "unique_narratives",
                "communities_active_in", "most_common_domain"):
        assert got[col].equals(exp[col]), f"{col} diverged from Gen-A"
    assert _as_text(got.first_seen).equals(exp.first_seen)
    assert _as_text(got.last_seen).equals(exp.last_seen)


def test_user_intel_fixes_the_gen_a_influence_disagreement(
    edges, influence, amplification_summary, committed_user_intel
):
    """The bug agents.py:83 documents, and the proof that we no longer have it.

    Gen-A recomputed the influence half locally as 2*narratives + 5*distance +
    5*amplification, with the amplification term read from the stale 65-author
    author_amplification_summary.csv. That fits its own file 192/192 -- and
    contradicts author_influence_profile.csv on 129 of 192 authors.
    """
    gen_a = committed_user_intel
    gen_a_formula = (
        gen_a.narratives_transported * 2
        + gen_a.avg_ideological_distance_crossed * 5
        + gen_a.total_relative_amplification * 5
    )
    assert np.allclose(gen_a.final_influence_score, gen_a_formula, atol=1e-6)

    gen_a_profile = pd.read_csv(DATA_DIR / "author_influence_profile.csv").set_index(
        "author"
    )
    agreed = np.isclose(
        gen_a.final_influence_score,
        gen_a.author.map(gen_a_profile.final_influence_score),
        atol=1e-6,
    ).sum()
    assert agreed == 63, f"expected the Gen-A tables to disagree on 129, got {agreed}"

    got = build_user_intelligence(edges, influence, amplification_summary)
    profile = influence.set_index("author")
    assert np.isclose(
        got.final_influence_score,
        got.author.map(profile.final_influence_score),
        atol=1e-12,
    ).all()


def test_user_intel_narratives_transported_matches_the_edge_count(
    edges, influence, amplification_summary
):
    """The profile counts narratives off the diffusion table, which is these edges."""
    got = build_user_intelligence(edges, influence, amplification_summary)
    assert (got.narratives_transported == got.unique_narratives).all()
