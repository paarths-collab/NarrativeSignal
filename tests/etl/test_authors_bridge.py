# tests/etl/test_authors_bridge.py
import pandas as pd

from etl.config import DATA_DIR
from etl.transforms.authors import build_bridge_authors


def test_reproduces_committed_artifact(golden_posts):
    got = build_bridge_authors(golden_posts)
    exp = pd.read_csv(DATA_DIR / "bridge_authors_v2.csv")
    assert len(got) == 178
    assert got.author.nunique() == 86
    pd.testing.assert_frame_equal(
        got.sort_values(["author", "subreddit"]).reset_index(drop=True),
        exp.sort_values(["author", "subreddit"]).reset_index(drop=True),
        check_dtype=False,
    )


def test_every_author_spans_at_least_two_subreddits(golden_posts):
    got = build_bridge_authors(golden_posts)
    assert (got.groupby("author").subreddit.nunique() >= 2).all()


def test_post_count_matches_raw_groupby(golden_posts):
    got = build_bridge_authors(golden_posts)
    raw = golden_posts.groupby(["author", "subreddit"]).size()
    for _, r in got.iterrows():
        assert int(raw[(r.author, r.subreddit)]) == int(r.post_count)
