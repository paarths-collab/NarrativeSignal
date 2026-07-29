"""Author-level transforms (spec §6.5)."""
from itertools import combinations

import numpy as np
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


INFLUENCE_COLUMNS = [
    "author", "total_posts", "narratives_transported",
    "avg_ideological_distance_crossed", "total_amplification_generated",
    "final_influence_score",
]

# Solved by coefficient search against all 3,598 committed rows. Only 5 fits the
# distance term; 1, 2, 3 and 10 all fail. The term hides because 3,511 of 3,598
# authors have avg_ideological_distance_crossed == 0.0.
AMPLIFICATION_DIVISOR = 100.0
NARRATIVES_COEFFICIENT = 2
DISTANCE_COEFFICIENT = 5


def build_amplification_summary(events: pd.DataFrame) -> pd.DataFrame:
    """author_amplification_summary, recomputed.

    The committed file is stale: none of its 65 authors reconcile with the event
    table it was supposedly derived from. Always recompute; never copy.
    """
    grouped = events.groupby("author")
    return (
        pd.DataFrame(
            {
                "total_relative_amplification": grouped["amplification_percentage"].sum(),
                "amplification_events": grouped.size(),
            }
        )
        .reset_index()
        .sort_values("author")
        .reset_index(drop=True)
    )


def _mean_pairwise_distance(subreddits, lookup: dict) -> float:
    """Mean ideological distance over an author's distinct subreddit pairs.

    0.0 for a single-subreddit author: there is no pair to cross. This is the
    bridge-author span, NOT a per-hop average over the event table -- see
    build_influence_profile for the evidence.
    """
    subs = sorted(set(subreddits))
    if len(subs) < 2:
        return 0.0
    pairs = combinations(subs, 2)
    return float(np.mean([lookup.get(frozenset(p), 0.0) for p in pairs]))


def build_influence_profile(
    posts: pd.DataFrame,
    diffusion: pd.DataFrame,
    events: pd.DataFrame,
    distances: pd.DataFrame,
) -> pd.DataFrame:
    """author_influence_profile with the verified composite score.

    Every column below was solved against the committed Gen-A artifacts, which are
    mutually consistent (same run), and fits 3,598 of 3,598 rows exactly:

    * author universe        <- the diffusion table's authors (exact set equality)
    * narratives_transported <- diffusion narrative_id.nunique() per author
                                (the event table gives only 1,509 of 3,598)
    * avg_ideological_distance_crossed <- mean distance over the author's DISTINCT
                                SUBREDDIT PAIRS, 0.0 below two subreddits. It is not
                                an event-table hop average: exactly 87 authors post
                                in >=2 subreddits and exactly 87 have a nonzero
                                value, 8 of which appear in no amplification event
                                at all, and every distinct nonzero value is a raw
                                matrix entry.
    * total_amplification_generated <- per-author event sum, 0.0 where no events
    * total_posts            <- per-author row count in `posts`

    Gen-A cannot distinguish the source of total_posts and the distance span: its
    diffusion table held all 8,687 clean rows and is per-author identical to the
    post table. `posts` is used for both as the semantic analogue of that full post
    table; today's diffusion table is a narrow 285-row subset and would understate
    a column named "total posts".

    The frozenset lookup is DEFENSIVE, not load-bearing: combinations(sorted(...))
    always emits canonical (a < b) order, so an ordered-tuple lookup also fits all
    3,598 rows. It is kept so a distance frame supplied in non-canonical order
    still resolves instead of silently returning 0.0.
    """
    lookup: dict[frozenset, float] = {
        frozenset((r.subreddit_a, r.subreddit_b)): float(r.ideological_distance)
        for r in distances.itertuples()
    }

    # The diffusion table defines the universe -- authors who moved a narrative.
    out = (
        diffusion.groupby("author")["narrative_id"]
        .nunique()
        .rename("narratives_transported")
        .reset_index()
    )

    spans = posts.groupby("author")["subreddit"].apply(
        lambda s: _mean_pairwise_distance(s, lookup)
    )
    out["avg_ideological_distance_crossed"] = (
        out["author"].map(spans).fillna(0.0).astype(float)
    )

    amplification = events.groupby("author")["amplification_percentage"].sum()
    out["total_amplification_generated"] = (
        out["author"].map(amplification).fillna(0.0).astype(float)
    )

    totals = posts.groupby("author").size()
    out["total_posts"] = out["author"].map(totals).fillna(0).astype(int)

    out["final_influence_score"] = (
        out["total_amplification_generated"] / AMPLIFICATION_DIVISOR
        + out["narratives_transported"] * NARRATIVES_COEFFICIENT
        + out["avg_ideological_distance_crossed"] * DISTANCE_COEFFICIENT
    )
    return out[INFLUENCE_COLUMNS].sort_values("author").reset_index(drop=True)
