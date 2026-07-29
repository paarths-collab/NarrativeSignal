"""Pairwise ideological distance between subreddits (spec §6.7)."""
from itertools import combinations

import numpy as np
import pandas as pd


def build_ideological_matrix(clean_posts: pd.DataFrame) -> pd.DataFrame:
    """1 - cosine similarity over raw domain-count vectors.

    Built from the 8,687-row set (909 domains). The 8,567 set has 899 domains and
    reproduces only 10 of 45 rows — the [deleted] authors contribute 10 domains that
    change every value.
    """
    matrix = (
        clean_posts.pivot_table(
            index="subreddit", columns="domain", values="id", aggfunc="count"
        )
        .fillna(0)
    )

    rows = []
    for a, b in combinations(sorted(matrix.index), 2):
        va, vb = matrix.loc[a].to_numpy(float), matrix.loc[b].to_numpy(float)
        denom = np.linalg.norm(va) * np.linalg.norm(vb)
        similarity = float(np.dot(va, vb) / denom) if denom else 0.0
        rows.append(
            {"subreddit_a": a, "subreddit_b": b, "ideological_distance": 1.0 - similarity}
        )
    return pd.DataFrame(rows)
