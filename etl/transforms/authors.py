"""Author-level transforms (spec §6.5)."""
import pandas as pd


def build_bridge_authors(posts: pd.DataFrame) -> pd.DataFrame:
    """bridge_authors_v2: cross-community posters, one row per (author, subreddit)."""
    spans = posts.groupby("author")["subreddit"].nunique()
    bridges = spans[spans >= 2].index
    return (
        posts[posts["author"].isin(bridges)]
        .groupby(["author", "subreddit"])
        .size()
        .rename("post_count")
        .reset_index()
    )
