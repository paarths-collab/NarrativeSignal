# tests/etl/test_authors_amplification.py
import numpy as np
import pandas as pd

from etl.config import DATA_DIR
from etl.transforms.authors import build_amplification_events, build_diffusion_table
from etl.transforms.dedupe import build_clusters_table
from etl.transforms.narratives import build_graph_edges


def _edges(golden_clean_posts):
    return build_graph_edges(build_clusters_table(golden_clean_posts))


def test_diffusion_is_one_row_per_clustered_post(golden_clean_posts):
    got = build_diffusion_table(_edges(golden_clean_posts))
    assert len(got) == 285
    assert got.narrative_id.nunique() == 154
    assert list(got.columns) == [
        "narrative_id", "subreddit", "author",
        "order_of_appearance", "time_from_origin_hours",
    ]


def test_order_is_zero_based_and_chronological(golden_clean_posts):
    got = build_diffusion_table(_edges(golden_clean_posts))
    for _, grp in got.groupby("narrative_id"):
        ordered = grp.sort_values("order_of_appearance")
        assert list(ordered.order_of_appearance) == list(range(len(grp)))
        assert ordered.time_from_origin_hours.is_monotonic_increasing
        assert ordered.time_from_origin_hours.iloc[0] == 0.0


def test_no_legacy_float_ids(golden_clean_posts):
    got = build_diffusion_table(_edges(golden_clean_posts))
    assert not got.narrative_id.str.contains(r"\.0_", regex=True).any()


def test_amplification_events_are_cross_subreddit_hops(golden_clean_posts):
    edges = _edges(golden_clean_posts)
    got = build_amplification_events(build_diffusion_table(edges), edges)
    assert list(got.columns) == [
        "author", "narrative_id", "from_sub", "to_sub", "time_delta_hours",
        "engagement_before", "engagement_after", "amplification_percentage",
    ]
    assert (got.from_sub != got.to_sub).all()
    assert (got.time_delta_hours >= 0).all()


def test_amplification_percentage_formula(golden_clean_posts):
    edges = _edges(golden_clean_posts)
    got = build_amplification_events(build_diffusion_table(edges), edges)
    nonzero = got[got.engagement_before != 0]
    expected = (nonzero.engagement_after - nonzero.engagement_before) \
        / nonzero.engagement_before * 100
    assert np.allclose(nonzero.amplification_percentage, expected, atol=1e-9)
    assert (got[got.engagement_before == 0].amplification_percentage == 0.0).all()


def test_engagement_is_the_specific_posts_score_at_each_hop(golden_clean_posts):
    """engagement_before/after must be the score of the two posts either side of the
    hop -- not an aggregate over the author's posts in that subreddit.

    The expected hops are rebuilt here straight from `edges` with plain-Python
    sorting, deliberately not reusing build_diffusion_table or
    build_amplification_events, so a bug shared by both would still be caught.
    """
    edges = _edges(golden_clean_posts)
    got = build_amplification_events(build_diffusion_table(edges), edges)

    # (narrative_id, created_datetime) is unique across edges, so this ordering is
    # unambiguous and cannot disagree with pandas' sort on a tie.
    assert not edges.duplicated(["narrative_id", "created_datetime"]).any()

    posts = sorted(
        (
            (r.narrative_id, r.created_datetime, r.subreddit, r.author, int(r.score))
            for r in edges.itertuples(index=False)
        ),
        key=lambda p: (p[0], p[1]),
    )

    by_narrative = {}
    for nid, created, sub, author, score in posts:
        by_narrative.setdefault(nid, []).append((sub, author, score))

    expected = []
    for nid, chain in by_narrative.items():
        for (psub, _pauth, pscore), (csub, cauth, cscore) in zip(chain, chain[1:]):
            if psub == csub:
                continue
            expected.append((cauth, nid, psub, csub, pscore, cscore))

    actual = [
        (r.author, r.narrative_id, r.from_sub, r.to_sub,
         int(r.engagement_before), int(r.engagement_after))
        for r in got.itertuples(index=False)
    ]
    assert actual == expected

    # Honest scope note: on the current data this test would also pass against a
    # groupby-max implementation. The only 2 posts whose score differs from their
    # (narrative_id, subreddit, author) max both sit in narrative 12_i.redd.it,
    # whose 4 posts are all in r/worldpolitics -- so every consecutive pair is
    # same-subreddit and the narrative contributes no hops at all. The divergence
    # is exercised directly by test_engagement_prefers_the_post_over_the_author_max.
    repeated = edges.duplicated(["narrative_id", "subreddit", "author"], keep=False)
    assert repeated.sum() == 4
    assert set(edges.loc[repeated, "narrative_id"]) == {"12_i.redd.it"}


def test_engagement_prefers_the_post_over_the_author_max():
    """Directly exercise a hop out of an author's *lower*-scoring repeat post.

    Synthetic, because the real data happens not to contain this shape (see the
    note in the test above). Without it, a regression to a groupby-max lookup
    would pass the whole suite.
    """
    edges = pd.DataFrame(
        {
            "narrative_id": ["1_x", "1_x", "1_x"],
            "cluster_id": [1, 1, 1],
            "author": ["alice", "alice", "bob"],
            "subreddit": ["subA", "subA", "subB"],
            "title": ["t", "t", "t"],
            "domain": ["x", "x", "x"],
            "url": ["u", "u", "u"],
            "permalink": ["p", "p", "p"],
            "created_datetime": pd.to_datetime(
                ["2025-01-01 00:00:00", "2025-01-01 01:00:00", "2025-01-01 02:00:00"]
            ),
            "score": [100, 10, 20],
            "num_comments": [0, 0, 0],
            "origin_subreddit": ["subA", "subA", "subA"],
            "hours_from_origin": [0.0, 1.0, 2.0],
        }
    )

    got = build_amplification_events(build_diffusion_table(edges), edges)

    assert len(got) == 1
    row = got.iloc[0]
    assert row.from_sub == "subA" and row.to_sub == "subB"
    # The hop leaves alice's SECOND post (score 10), not her best one (score 100).
    assert row.engagement_before == 10
    assert row.engagement_after == 20
    assert row.amplification_percentage == 100.0


def test_formula_matches_committed_gen_a_rows():
    """The formula is verified against Gen-A even though the ids differ."""
    exp = pd.read_csv(DATA_DIR / "amplification_event_table.csv")
    nz = exp[exp.engagement_before != 0]
    assert np.allclose(
        nz.amplification_percentage,
        (nz.engagement_after - nz.engagement_before) / nz.engagement_before * 100,
        atol=1e-6,
    )
    assert (exp[exp.engagement_before == 0].amplification_percentage == 0.0).all()
