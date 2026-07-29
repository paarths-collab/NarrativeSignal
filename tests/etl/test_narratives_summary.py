import pandas as pd

from etl.config import DATA_DIR
from etl.transforms.dedupe import build_clusters_table
from etl.transforms.narratives import build_graph_edges, build_narrative_summary


def _summary(golden_clean_posts):
    return build_narrative_summary(build_graph_edges(build_clusters_table(golden_clean_posts)))


def test_reproduces_committed_artifact(golden_clean_posts):
    got = _summary(golden_clean_posts)
    exp = pd.read_csv(DATA_DIR / "narrative_intelligence_summary.csv")
    assert len(got) == 154
    assert list(got.columns) == list(exp.columns)
    merged = got.merge(exp, on="narrative_id", suffixes=("_g", "_e"))
    assert len(merged) == 154
    for col in ["total_posts_x", "unique_subreddits_x", "unique_authors",
                "cluster_id", "spread_strength"]:
        assert (merged[f"{col}_g"] == merged[f"{col}_e"]).all(), col
    assert (merged.representative_title_g == merged.representative_title_e).all()
    assert (merged.primary_domain_g == merged.primary_domain_e).all()


def test_representative_title_is_highest_scoring_post(golden_clean_posts):
    edges = build_graph_edges(build_clusters_table(golden_clean_posts))
    got = _summary(golden_clean_posts).set_index("narrative_id")
    for nid, grp in edges.groupby("narrative_id"):
        assert got.loc[nid, "representative_title"] == grp.loc[grp.score.idxmax(), "title"]


def test_spread_strength_is_posts_times_subreddits(golden_clean_posts):
    got = _summary(golden_clean_posts)
    assert (got.spread_strength == got.total_posts_x * got.unique_subreddits_x).all()


def test_x_and_y_columns_are_identical(golden_clean_posts):
    """Vestigial self-merge. Production reads only _x, but both are contractual."""
    got = _summary(golden_clean_posts)
    assert (got.total_posts_x == got.total_posts_y).all()
    assert (got.unique_subreddits_x == got.unique_subreddits_y).all()
    assert (got.first_seen_x == got.first_seen_y).all()


def test_timestamps_match_committed(golden_clean_posts):
    got = _summary(golden_clean_posts)
    exp = pd.read_csv(DATA_DIR / "narrative_intelligence_summary.csv")
    merged = got.merge(exp, on="narrative_id", suffixes=("_g", "_e"))
    for col in ["first_seen_x", "last_seen"]:
        assert (
            pd.to_datetime(merged[f"{col}_g"]) == pd.to_datetime(merged[f"{col}_e"])
        ).all(), col
