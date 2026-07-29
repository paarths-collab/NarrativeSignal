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


DIFFUSION_COLUMNS = [
    "narrative_id", "subreddit", "author",
    "order_of_appearance", "time_from_origin_hours",
]

AMPLIFICATION_COLUMNS = [
    "author", "narrative_id", "from_sub", "to_sub", "time_delta_hours",
    "engagement_before", "engagement_after", "amplification_percentage",
]


def build_diffusion_table(edges: pd.DataFrame) -> pd.DataFrame:
    """narrative_diffusion_table: each post's position in its narrative's spread.

    Regenerated in the current id format: 154 narratives over 285 posts. The committed
    Gen-A file covered 1,055 narratives because it clustered on exact title alone,
    without the >=2-distinct-authors requirement now enforced in dedupe.py, so it
    admitted single-*cluster* headlines this table drops. Note that 41 of the 154
    narratives here are still single-post, because a multi-post cluster spanning two
    domains can split into narratives that each hold one post.
    """
    out = edges.sort_values(["narrative_id", "created_datetime"]).copy()
    out["order_of_appearance"] = out.groupby("narrative_id").cumcount()
    out["time_from_origin_hours"] = out["hours_from_origin"]
    return out[DIFFUSION_COLUMNS].reset_index(drop=True)


def build_amplification_events(
    diffusion: pd.DataFrame, edges: pd.DataFrame
) -> pd.DataFrame:
    """amplification_event_table: consecutive cross-subreddit hops of a narrative.

    engagement_before/after are the score of the posts either side of the hop.
    """
    # build_diffusion_table is a positional slice of edges sorted on these same keys,
    # so sorting identically here aligns score row-for-row with the diffusion rows.
    # Carrying the score through that alignment -- rather than looking it up by
    # (narrative_id, subreddit, author) -- is what makes engagement_before/after the
    # score of the specific post at the hop. An author who posts the same narrative
    # into the same subreddit twice would otherwise collapse into one lookup key.
    ordered = edges.sort_values(["narrative_id", "created_datetime"]).reset_index(
        drop=True
    )
    aligned = diffusion.reset_index(drop=True)

    # Raised, not asserted: `python -O` strips assert statements, and a stripped
    # guard here would silently attribute scores to the wrong posts.
    if len(ordered) != len(aligned):
        raise ValueError(
            f"diffusion has {len(aligned)} rows but edges has {len(ordered)}; "
            "build_diffusion_table and build_amplification_events have drifted apart"
        )
    for col in ("narrative_id", "subreddit", "author"):
        if not aligned[col].equals(ordered[col]):
            raise ValueError(
                f"column {col!r} does not align between the diffusion table and edges "
                "sorted on (narrative_id, created_datetime); the two functions have "
                "drifted apart and the scores would be attributed to the wrong posts"
            )
    aligned = aligned.assign(score=ordered["score"].to_numpy())

    rows = []
    for nid, grp in aligned.sort_values("order_of_appearance").groupby("narrative_id"):
        records = grp.to_dict("records")
        for prev, cur in zip(records, records[1:]):
            if prev["subreddit"] == cur["subreddit"]:
                continue
            before = int(prev["score"])
            after = int(cur["score"])
            pct = ((after - before) / before * 100.0) if before else 0.0
            rows.append(
                {
                    "author": cur["author"],
                    "narrative_id": nid,
                    "from_sub": prev["subreddit"],
                    "to_sub": cur["subreddit"],
                    "time_delta_hours": cur["time_from_origin_hours"]
                    - prev["time_from_origin_hours"],
                    "engagement_before": before,
                    "engagement_after": after,
                    "amplification_percentage": pct,
                }
            )
    return pd.DataFrame(rows, columns=AMPLIFICATION_COLUMNS)
