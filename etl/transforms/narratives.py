"""Narrative-level transforms (spec §6.3)."""
import pandas as pd

GRAPH_EDGE_COLUMNS = [
    "narrative_id", "cluster_id", "author", "subreddit", "title", "domain",
    "url", "permalink", "created_datetime", "score", "num_comments",
    "origin_subreddit", "hours_from_origin",
]


def build_graph_edges(clusters: pd.DataFrame) -> pd.DataFrame:
    """graph_edge_intelligence_table: one row per clustered post.

    A cluster is one headline; splitting it by domain yields the narrative. 62 of
    131 clusters span more than one domain, so 131 clusters -> 154 narrative_ids.
    """
    edges = clusters[clusters["duplicate_cluster_id"].notna()].copy()
    edges["cluster_id"] = edges["duplicate_cluster_id"].astype(int)
    edges["narrative_id"] = edges["cluster_id"].astype(str) + "_" + edges["domain"]
    edges["created_datetime"] = pd.to_datetime(edges["created_datetime"])

    grouped = edges.groupby("narrative_id")
    origin_time = grouped["created_datetime"].transform("min")
    edges["hours_from_origin"] = (
        edges["created_datetime"] - origin_time
    ).dt.total_seconds() / 3600.0

    origin_sub = (
        edges.loc[grouped["created_datetime"].idxmin()]
        .set_index("narrative_id")["subreddit"]
    )
    edges["origin_subreddit"] = edges["narrative_id"].map(origin_sub)

    return edges[GRAPH_EDGE_COLUMNS].reset_index(drop=True)
