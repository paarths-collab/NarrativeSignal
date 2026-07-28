"""Narrative clustering (spec §6.2).

A narrative is one headline posted by *different people*. Exact-title match with a
>=2-distinct-authors requirement; no embeddings and no similarity threshold.
"""
import pandas as pd

from etl.config import EXCLUDED_AUTHORS_POSTS


def assign_clusters(clean_posts: pd.DataFrame) -> pd.DataFrame:
    """Return only the clustered rows of `clean_posts`, with duplicate_cluster_id.

    Runs on the 8,687-row set — before [deleted] is filtered — which is why a
    cluster can end up a singleton downstream.
    """
    grouped = clean_posts.groupby("title")
    mask = (grouped["id"].transform("size") > 1) & (
        grouped["author"].transform("nunique") > 1
    )
    clustered = clean_posts[mask].copy()

    # Ids are assigned in alphabetical title order (pandas groupby sorts keys).
    order = {title: i for i, title in enumerate(sorted(clustered["title"].unique()))}
    clustered["duplicate_cluster_id"] = clustered["title"].map(order).astype(float)
    return clustered


def build_clusters_table(clean_posts: pd.DataFrame) -> pd.DataFrame:
    """clean_with_clusters_v2: the 8,567 set with a nullable cluster column."""
    clustered = assign_clusters(clean_posts)
    ids = clustered.set_index("id")["duplicate_cluster_id"]

    posts = clean_posts[~clean_posts["author"].isin(EXCLUDED_AUTHORS_POSTS)].copy()
    posts["duplicate_cluster_id"] = posts["id"].map(ids)
    return posts.reset_index(drop=True)
