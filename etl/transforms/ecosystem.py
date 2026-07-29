"""Media-ecosystem transforms (spec §6.4)."""
import pandas as pd


def build_domain_flow(posts: pd.DataFrame) -> pd.DataFrame:
    """subreddit_domain_flow_v2: post counts per (subreddit, domain), 8,567 set.

    NaN domains are dropped by groupby default — the committed artifact has 1,337
    rows summing to 8,479, which is 8,567 minus the 88 null-domain posts.
    """
    return (
        posts.groupby(["subreddit", "domain"])
        .size()
        .rename("count")
        .reset_index()
    )
