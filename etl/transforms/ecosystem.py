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


MIN_DOMAIN_COUNT = 5
TOP_N_PER_SUBREDDIT = 5


def build_distinctive_domains(
    posts: pd.DataFrame, allowed: dict[str, str]
) -> pd.DataFrame:
    """clean_top_distinctive_domains: lift of each domain within its subreddit.

    `allowed` maps domain -> category and defines the media universe. Posts linking
    outside it are excluded from every denominator, which is why Anarchism counts
    101 of its 394 link posts while politics counts 744 of 990.

    The original allowlist is lost, so this artifact is not byte-reproducible; the
    arithmetic below is what was verified.
    """
    universe = posts[posts["domain"].isin(allowed)].copy()

    counts = universe.groupby(["subreddit", "domain"]).size().rename("count").reset_index()
    sub_total = universe.groupby("subreddit").size().rename("sub_total")
    domain_total = universe.groupby("domain").size().rename("domain_total")
    global_total = int(sub_total.sum())

    out = counts.join(sub_total, on="subreddit").join(domain_total, on="domain")
    out["category"] = out["domain"].map(allowed)
    out["p_domain_given_sub"] = out["count"] / out["sub_total"]
    out["p_domain_global"] = out["domain_total"] / global_total
    out["lift"] = out["p_domain_given_sub"] / out["p_domain_global"]

    out = out[out["count"] >= MIN_DOMAIN_COUNT]
    out = (
        out.sort_values(["subreddit", "lift"], ascending=[True, False])
        .groupby("subreddit", sort=False)
        .head(TOP_N_PER_SUBREDDIT)
        .reset_index(drop=True)
    )
    return out[
        ["subreddit", "domain", "count", "category", "sub_total", "domain_total",
         "p_domain_given_sub", "p_domain_global", "lift"]
    ]


def build_echo_chambers(distinctive: pd.DataFrame) -> pd.DataFrame:
    """echo_chamber_scores: mean of a subreddit's surviving top-5 lifts.

    Sensitive by construction — worldpolitics scores 9.19 off just two domains.
    """
    return (
        distinctive.groupby("subreddit")["lift"].mean().rename("lift").reset_index()
    )
