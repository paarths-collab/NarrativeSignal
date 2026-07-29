import ast

import pandas as pd

from etl.config import DATA_DIR
from etl.transforms.dedupe import build_clusters_table
from etl.transforms.narratives import (
    build_graph_edges, build_spread_chains, build_topic_mapping,
)


def _edges(golden_clean_posts):
    return build_graph_edges(build_clusters_table(golden_clean_posts))


def test_spread_chains_reproduce_committed_artifact(golden_clean_posts):
    got = build_spread_chains(_edges(golden_clean_posts))
    exp = pd.read_csv(DATA_DIR / "narrative_spread_chain_table.csv")
    assert len(got) == 154
    merged = got.merge(exp, on="narrative_id", suffixes=("_g", "_e"))
    assert len(merged) == 154
    assert (merged.spread_sequence_g == merged.spread_sequence_e).all()


def test_duplicates_are_retained(golden_clean_posts):
    """Deduplicating by first appearance breaks 41/154 rows."""
    got = build_spread_chains(_edges(golden_clean_posts))
    seqs = got.spread_sequence.map(ast.literal_eval)
    assert (seqs.map(len) > seqs.map(lambda s: len(set(s)))).sum() > 0


def test_serialization_format(golden_clean_posts):
    """subreddit.py:390 requires exactly this shape."""
    got = build_spread_chains(_edges(golden_clean_posts))
    assert got.spread_sequence.str.match(r"^\['.*'\]$").all()
    multi = got[got.spread_sequence.str.contains(",")]
    assert multi.spread_sequence.str.contains("', '").all()


def test_sequence_length_equals_narrative_post_count(golden_clean_posts):
    edges = _edges(golden_clean_posts)
    got = build_spread_chains(edges).set_index("narrative_id")
    sizes = edges.groupby("narrative_id").size()
    for nid, seq in got.spread_sequence.items():
        assert len(ast.literal_eval(seq)) == sizes[nid]


def test_topic_mapping_structure(golden_clean_posts):
    got = build_topic_mapping(_edges(golden_clean_posts), n_clusters=5, seed=42)
    assert len(got) == 154
    assert list(got.columns) == ["narrative_id", "topic_cluster", "topic_label"]
    assert set(got.topic_cluster) <= {0, 1, 2, 3, 4}
    assert got.topic_label.str.match(r"^LABEL_ME_[0-4]$").all()
    rebuilt = "LABEL_ME_" + got.topic_cluster.astype(str)
    assert (got.topic_label == rebuilt).all()


def test_topic_mapping_is_deterministic(golden_clean_posts):
    """Same seed must give the same assignment, or nightly runs churn."""
    edges = _edges(golden_clean_posts)
    a = build_topic_mapping(edges, n_clusters=5, seed=42)
    b = build_topic_mapping(edges, n_clusters=5, seed=42)
    assert (a.topic_cluster.values == b.topic_cluster.values).all()


def test_topic_label_matches_committed_placeholder_format(golden_clean_posts):
    """Values differ (no MiniLM), but the LABEL_ME_ format is contractual."""
    exp = pd.read_csv(DATA_DIR / "narrative_topic_mapping.csv")
    assert exp.topic_label.str.match(r"^LABEL_ME_[0-4]$").all()
    got = build_topic_mapping(_edges(golden_clean_posts))
    assert set(got.narrative_id) == set(exp.narrative_id)
