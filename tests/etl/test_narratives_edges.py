import numpy as np
import pandas as pd

from etl.config import DATA_DIR
from etl.transforms.dedupe import build_clusters_table
from etl.transforms.narratives import build_graph_edges


def test_reproduces_committed_artifact(golden_clean_posts):
    got = build_graph_edges(build_clusters_table(golden_clean_posts))
    exp = pd.read_csv(DATA_DIR / "graph_edge_intelligence_table.csv")
    assert len(got) == 285
    assert got.narrative_id.nunique() == 154
    # no `id` column in this artifact; permalink is the unique key
    assert exp.permalink.is_unique and got.permalink.is_unique
    merged = got.merge(exp, on="permalink", suffixes=("_got", "_exp"))
    assert len(merged) == 285
    assert (merged.narrative_id_got == merged.narrative_id_exp).all()
    assert (merged.origin_subreddit_got == merged.origin_subreddit_exp).all()
    assert np.allclose(merged.hours_from_origin_got, merged.hours_from_origin_exp,
                       atol=1e-9)


def test_narrative_id_is_cluster_underscore_domain(golden_clean_posts):
    got = build_graph_edges(build_clusters_table(golden_clean_posts))
    rebuilt = got.cluster_id.astype(int).astype(str) + "_" + got.domain
    assert (got.narrative_id == rebuilt).all()


def test_no_legacy_float_ids(golden_clean_posts):
    """'0.0_salon.com' is the retired Gen-A form and breaks every join."""
    got = build_graph_edges(build_clusters_table(golden_clean_posts))
    assert not got.narrative_id.str.contains(r"\.0_", regex=True).any()


def test_clusters_split_by_domain(golden_clean_posts):
    """62 clusters span >1 domain; 131 clusters therefore yield 154 narratives."""
    got = build_graph_edges(build_clusters_table(golden_clean_posts))
    assert got.cluster_id.nunique() == 131
    assert got.narrative_id.nunique() == 154


def test_origin_is_earliest_post_in_the_narrative(golden_clean_posts):
    got = build_graph_edges(build_clusters_table(golden_clean_posts))
    for nid, grp in got.groupby("narrative_id"):
        earliest = grp.loc[grp.created_datetime.idxmin()]
        assert (grp.origin_subreddit == earliest.subreddit).all()
        assert grp.hours_from_origin.min() == 0.0


def test_column_order(golden_clean_posts):
    got = build_graph_edges(build_clusters_table(golden_clean_posts))
    assert list(got.columns) == [
        "narrative_id", "cluster_id", "author", "subreddit", "title", "domain",
        "url", "permalink", "created_datetime", "score", "num_comments",
        "origin_subreddit", "hours_from_origin",
    ]
