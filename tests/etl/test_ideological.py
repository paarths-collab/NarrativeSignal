# tests/etl/test_ideological.py
import numpy as np
import pandas as pd

from etl.config import DATA_DIR
from etl.transforms.ideological import build_ideological_matrix


def test_reproduces_committed_artifact(golden_clean_posts):
    got = build_ideological_matrix(golden_clean_posts)
    exp = pd.read_csv(DATA_DIR / "ideological_distance_matrix.csv")
    assert len(got) == 45
    merged = got.merge(exp, on=["subreddit_a", "subreddit_b"], suffixes=("_got", "_exp"))
    assert len(merged) == 45
    assert np.allclose(
        merged.ideological_distance_got, merged.ideological_distance_exp, atol=1e-12
    )


def test_wrong_cascade_level_breaks_it(golden_posts, golden_clean_posts):
    """Guards the single easiest mistake in this transform."""
    right = build_ideological_matrix(golden_clean_posts)
    wrong = build_ideological_matrix(golden_posts)
    merged = right.merge(wrong, on=["subreddit_a", "subreddit_b"], suffixes=("_r", "_w"))
    exact = np.isclose(
        merged.ideological_distance_r, merged.ideological_distance_w, atol=1e-12
    ).sum()
    assert exact < 45, "8,567 must NOT reproduce the committed values"


def test_upper_triangle_alphabetical(golden_clean_posts):
    got = build_ideological_matrix(golden_clean_posts)
    assert (got.subreddit_a < got.subreddit_b).all()
    assert len(got) == 45  # C(10,2)
