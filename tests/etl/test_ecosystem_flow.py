# tests/etl/test_ecosystem_flow.py
import pandas as pd

from etl.config import DATA_DIR
from etl.transforms.ecosystem import build_domain_flow


def test_reproduces_committed_artifact(golden_posts):
    got = build_domain_flow(golden_posts)
    exp = pd.read_csv(DATA_DIR / "subreddit_domain_flow_v2.csv")
    assert len(got) == 1337
    pd.testing.assert_frame_equal(
        got.sort_values(["subreddit", "domain"]).reset_index(drop=True),
        exp.sort_values(["subreddit", "domain"]).reset_index(drop=True),
        check_dtype=False,
    )


def test_counts_are_integers(golden_posts):
    """int(row['count']) at backend/polarize_1/data_loader.py raises on floats."""
    got = build_domain_flow(golden_posts)
    assert pd.api.types.is_integer_dtype(got["count"])
