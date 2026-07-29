"""Chatbot-facing rollups (spec §6.6).

Three artifacts the retrieval layer reads directly: a narrative lookup table, a
per-subreddit card and a per-author card. All three are regenerated in the current
integer narrative-id format (`0_salon.com`), retiring the
`str(nid).replace(".0_", "_")` bridge production used against the Gen-A files.

All three reconcile against the committed Gen-A artifacts, so the formulas below are
observed rather than guessed -- see tests/etl/test_chatbot.py:

* narrative_registry: the full 1,055-row Gen-A file is reproduced row for row, in
  file order, by running build_narrative_registry over a frame whose 901 unclustered
  posts fall back to a `domain_domain` id. Its 154 clustered rows are then identical
  to what these 154 narratives produce today.
* subreddit_intelligence_summary: reproduced exactly, 9 rows x 7 columns. It never
  depended on the id format -- its numbers already came from the 285-row edge table.
* user_intelligence_summary: the seven duplicate-post columns reproduce 192/192. The
  five influence columns intentionally do not; see build_user_intelligence.
"""
import pandas as pd

REGISTRY_COLUMNS = [
    "narrative_id", "cluster_id", "primary_domain", "representative_title",
    "total_posts", "unique_subreddits", "first_seen",
]


def build_narrative_registry(edges: pd.DataFrame) -> pd.DataFrame:
    """narrative_registry: one row per narrative, the chatbot's id lookup.

    154 rows, against Gen-A's 1,055. Gen-A ran this same aggregation over all 8,687
    clean posts, giving every *unclustered* post a `domain_domain` pseudo-narrative;
    901 of its rows are those buckets and carry no cluster at all. Only the 154
    clustered rows are narratives in the current sense, and they survive the
    regeneration unchanged apart from the id.

    representative_title is the FIRST post in the group, in table order -- not the
    highest-scoring one. build_narrative_summary picks by score and the two disagree;
    the max-score rule fits only 816 of Gen-A's 1,055 rows, `first` fits all 1,055.
    Do not "fix" this to match the summary: the registry is the file the chatbot
    quotes back, and its titles are the ones already in the vector index.
    """
    grouped = edges.groupby("narrative_id")
    out = grouped.agg(
        cluster_id=("cluster_id", "first"),
        primary_domain=("domain", "first"),
        representative_title=("title", "first"),
        total_posts=("subreddit", "size"),
        unique_subreddits=("subreddit", "nunique"),
        first_seen=("created_datetime", "min"),
    ).reset_index()
    return out[REGISTRY_COLUMNS]


SUBREDDIT_INTEL_COLUMNS = [
    "subreddit", "total_duplicate_posts", "unique_narratives", "unique_users",
    "avg_score", "domain", "lift",
]

TOP_DOMAINS = 3


def build_subreddit_intelligence(
    edges: pd.DataFrame, echo_chambers: pd.DataFrame
) -> pd.DataFrame:
    """subreddit_intelligence_summary: 9 rows, one per subreddit carrying duplicates.

    Nine, not ten -- PoliticalDiscussion contributes no clustered post, so it never
    enters the edge table and has no echo-chamber score either.

    `domain` is the top-3 domains as a STRINGIFIED PYTHON LIST, the same str(list)
    hazard as spread_sequence: consumers ast.literal_eval it. Ties on count are
    broken alphabetically, which is load-bearing -- Liberal's six posts are six
    distinct domains, so every candidate ties and only the alphabetical rule
    reproduces `['americanprogress.org', 'apnews.com', 'axios.com']`.

    `lift` is the subreddit's echo-chamber score, passed in rather than recomputed:
    build_echo_chambers depends on the lost domain allowlist, so this function must
    not re-derive it and quietly disagree with echo_chamber_scores.
    """
    out = edges.groupby("subreddit").agg(
        total_duplicate_posts=("narrative_id", "size"),
        unique_narratives=("narrative_id", "nunique"),
        unique_users=("author", "nunique"),
        avg_score=("score", "mean"),
    ).reset_index()

    counts = (
        edges.groupby(["subreddit", "domain"])
        .size()
        .rename("posts")
        .reset_index()
        .sort_values(["subreddit", "posts", "domain"], ascending=[True, False, True])
    )
    top = counts.groupby("subreddit")["domain"].apply(
        lambda s: str(list(s.head(TOP_DOMAINS)))
    )
    out["domain"] = out["subreddit"].map(top).fillna("[]")

    out["lift"] = (
        out["subreddit"]
        .map(echo_chambers.set_index("subreddit")["lift"])
        .astype(float)
    )
    return out[SUBREDDIT_INTEL_COLUMNS]


USER_INTEL_COLUMNS = [
    "author", "total_duplicate_posts", "unique_narratives", "communities_active_in",
    "first_seen", "last_seen", "most_common_domain", "total_posts",
    "narratives_transported", "avg_ideological_distance_crossed",
    "total_relative_amplification", "final_influence_score",
]


def _most_common(domains: pd.Series):
    """Modal domain, alphabetical on ties -- which is what Series.mode gives.

    value_counts() would break ties by first appearance and fits only 183 of the 192
    Gen-A rows; mode() fits all 192.
    """
    modes = domains.mode()
    return modes.iloc[0] if len(modes) else None


def build_user_intelligence(
    edges: pd.DataFrame,
    influence: pd.DataFrame,
    amplification_summary: pd.DataFrame,
) -> pd.DataFrame:
    """user_intelligence_summary: 192 authors who moved a clustered narrative.

    The first seven columns are duplicate-post stats off the edge table and match
    Gen-A exactly. The influence half does NOT, deliberately.

    Gen-A recomputed total_posts, narratives_transported,
    avg_ideological_distance_crossed and final_influence_score here from the
    duplicate-post world instead of reading author_influence_profile, and scored with
    2*narratives + 5*distance + 5*amplification where the amplification term came from
    the stale 65-author author_amplification_summary.csv. The result contradicted
    author_influence_profile on 129 of 192 authors, which is why
    backend/hybrid/agents.py:83 warns "ALWAYS use author_influence table, not
    user_intelligence". Sourcing all four from the profile makes that table the single
    source of truth and the warning obsolete.

    total_relative_amplification likewise comes from the recomputed amplification
    summary, whose per-author sum is by construction the profile's
    total_amplification_generated -- so the score's own amplification term is the
    number displayed beside it. Authors with no cross-subreddit hop score 0.0.
    """
    out = edges.groupby("author").agg(
        total_duplicate_posts=("narrative_id", "size"),
        unique_narratives=("narrative_id", "nunique"),
        communities_active_in=("subreddit", "nunique"),
        first_seen=("created_datetime", "min"),
        last_seen=("created_datetime", "max"),
        most_common_domain=("domain", _most_common),
    ).reset_index()

    profile = influence.set_index("author")
    for col in (
        "total_posts", "narratives_transported",
        "avg_ideological_distance_crossed", "final_influence_score",
    ):
        out[col] = out["author"].map(profile[col])

    out["total_relative_amplification"] = (
        out["author"]
        .map(amplification_summary.set_index("author")["total_relative_amplification"])
        .fillna(0.0)
        .astype(float)
    )
    return out[USER_INTEL_COLUMNS]
