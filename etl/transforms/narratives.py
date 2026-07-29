"""Narrative-level transforms (spec §6.3)."""
import hashlib
import re

import numpy as np
import pandas as pd

GRAPH_EDGE_COLUMNS = [
    "narrative_id", "cluster_id", "author", "subreddit", "title", "domain",
    "url", "permalink", "created_datetime", "score", "num_comments",
    "origin_subreddit", "hours_from_origin",
]


def build_graph_edges(clusters: pd.DataFrame) -> pd.DataFrame:
    """graph_edge_intelligence_table: one row per clustered post.

    A cluster is one headline; splitting it by domain yields the narrative. 22 of
    131 clusters span more than one domain (21 with two, 1 with three), so
    109 + 21*2 + 1*3 = 154 narrative_ids.
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


SUMMARY_COLUMNS = [
    "narrative_id", "total_posts_x", "unique_subreddits_x", "unique_authors",
    "first_seen_x", "last_seen", "cluster_id", "primary_domain",
    "representative_title", "total_posts_y", "unique_subreddits_y",
    "first_seen_y", "spread_strength",
]


def build_narrative_summary(edges: pd.DataFrame) -> pd.DataFrame:
    """narrative_intelligence_summary, grouped from graph_edge_intelligence_table."""
    grouped = edges.groupby("narrative_id")
    out = grouped.agg(
        total_posts_x=("author", "size"),
        unique_subreddits_x=("subreddit", "nunique"),
        unique_authors=("author", "nunique"),
        first_seen_x=("created_datetime", "min"),
        last_seen=("created_datetime", "max"),
        cluster_id=("cluster_id", "first"),
        primary_domain=("domain", "first"),
    ).reset_index()

    best = edges.loc[grouped["score"].idxmax()].set_index("narrative_id")["title"]
    out["representative_title"] = out["narrative_id"].map(best)

    # Vestigial self-merge duplicates: identical to the _x columns by construction.
    out["total_posts_y"] = out["total_posts_x"]
    out["unique_subreddits_y"] = out["unique_subreddits_x"]
    out["first_seen_y"] = out["first_seen_x"]

    out["spread_strength"] = out["total_posts_x"] * out["unique_subreddits_x"]
    return out[SUMMARY_COLUMNS]


_EMBED_DIM = 384


def build_spread_chains(edges: pd.DataFrame) -> pd.DataFrame:
    """narrative_spread_chain_table: subreddits in chronological order.

    Duplicates are RETAINED — '103_vox.com' is ['politics', 'politics']. Serialized
    with Python str(list) because backend/networkgraph/routers/subreddit.py:390
    parses exactly that shape and silently yields nothing otherwise.
    """
    sequences = (
        edges.sort_values("created_datetime")
        .groupby("narrative_id")["subreddit"]
        .apply(list)
    )
    return pd.DataFrame(
        {"narrative_id": sequences.index, "spread_sequence": sequences.map(str).values}
    )


def _hash_embed(text: str) -> np.ndarray:
    """Deterministic offline embedding, mirroring streamgraph2/models/embedder.py.

    The original pipeline used all-MiniLM-L6-v2, which needs a model download. Topic
    ids therefore differ from the committed file by design; only the *shape* of
    narrative_topic_mapping is contractual, and a later workflow replaces the labels.
    """
    vec = np.zeros(_EMBED_DIM, dtype="float32")
    for token in re.findall(r"[a-z0-9]+", str(text).lower()):
        if len(token) < 2:
            continue
        vec[int(hashlib.md5(token.encode()).hexdigest(), 16) % _EMBED_DIM] += 1.0
    norm = np.linalg.norm(vec)
    return vec / norm if norm else vec


def build_topic_mapping(
    edges: pd.DataFrame, n_clusters: int = 5, seed: int = 42
) -> pd.DataFrame:
    """narrative_topic_mapping with LABEL_ME_* placeholders.

    The committed labels are placeholders for a step that was specced and never run.
    """
    from sklearn.cluster import KMeans

    best = (
        edges.loc[edges.groupby("narrative_id")["score"].idxmax()]
        .set_index("narrative_id")["title"]
        .sort_index()
    )
    matrix = np.vstack([_hash_embed(t) for t in best])
    labels = KMeans(n_clusters=n_clusters, random_state=seed, n_init=10).fit_predict(matrix)

    return pd.DataFrame(
        {
            "narrative_id": best.index,
            "topic_cluster": labels,
            "topic_label": ["LABEL_ME_" + str(c) for c in labels],
        }
    ).reset_index(drop=True)
