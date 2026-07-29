# tests/etl/test_authors_influence.py
import numpy as np
import pandas as pd
import pytest

from etl.config import DATA_DIR
from etl.transforms.authors import (
    build_amplification_summary, build_influence_profile,
)

INFLUENCE_COLUMNS = [
    "author", "total_posts", "narratives_transported",
    "avg_ideological_distance_crossed", "total_amplification_generated",
    "final_influence_score",
]


def test_influence_formula_matches_committed_rows():
    """Verified against all 3,598 Gen-A rows; coefficients are 100, 2, 5."""
    exp = pd.read_csv(DATA_DIR / "author_influence_profile.csv")
    expected = (
        exp.total_amplification_generated / 100.0
        + exp.narratives_transported * 2
        + exp.avg_ideological_distance_crossed * 5
    )
    assert np.allclose(exp.final_influence_score, expected, atol=1e-6)


def test_no_other_distance_coefficient_fits():
    exp = pd.read_csv(DATA_DIR / "author_influence_profile.csv")
    base = exp.total_amplification_generated / 100.0 + exp.narratives_transported * 2
    for bad in (1, 2, 3, 10):
        cand = base + exp.avg_ideological_distance_crossed * bad
        assert not np.allclose(exp.final_influence_score, cand, atol=1e-6)


def test_gen_a_reconciliation_reproduces_committed_profile():
    """The real guard: rebuild the committed profile from the committed inputs.

    narrative_diffusion_table, amplification_event_table, ideological_distance_matrix
    and author_influence_profile are all Gen-A artifacts from one run, so their
    narrative ids are mutually consistent and the profile is reproducible row for
    row even though the ids differ from the current format.
    """
    clean = pd.read_csv(DATA_DIR / "clean_posts.csv")
    diffusion = pd.read_csv(DATA_DIR / "narrative_diffusion_table.csv")
    events = pd.read_csv(DATA_DIR / "amplification_event_table.csv")
    distances = pd.read_csv(DATA_DIR / "ideological_distance_matrix.csv")
    exp = (
        pd.read_csv(DATA_DIR / "author_influence_profile.csv")
        .sort_values("author")
        .reset_index(drop=True)
    )

    got = build_influence_profile(clean, diffusion, events, distances)

    assert len(got) == 3598
    assert got.author.equals(exp.author)
    for col in INFLUENCE_COLUMNS[1:]:
        assert np.allclose(got[col], exp[col], atol=1e-6), f"{col} diverged"


def test_author_universe_is_the_diffusion_table(
    golden_posts, sample_diffusion, sample_events, sample_distances
):
    """Regression for the original bug: the universe is diffusion, not events.

    Deriving it from the event table yielded 63 authors and dropped every author
    who moved a narrative without producing a cross-subreddit amplification hop.
    """
    got = build_influence_profile(
        golden_posts, sample_diffusion, sample_events, sample_distances
    )
    assert set(got.author) == set(sample_diffusion.author)
    assert len(got) == sample_diffusion.author.nunique() == 192
    assert len(got) > sample_events.author.nunique()
    # Authors with no amplification event are retained with a 0.0 contribution.
    assert (got.total_amplification_generated == 0).sum() == 130


def test_profile_columns_and_score(
    golden_posts, sample_diffusion, sample_events, sample_distances
):
    got = build_influence_profile(
        golden_posts, sample_diffusion, sample_events, sample_distances
    )
    assert list(got.columns) == INFLUENCE_COLUMNS
    expected = (
        got.total_amplification_generated / 100.0
        + got.narratives_transported * 2
        + got.avg_ideological_distance_crossed * 5
    )
    assert np.allclose(got.final_influence_score, expected, atol=1e-9)


def test_distance_is_subreddit_span_not_hop_average(
    golden_posts, sample_diffusion, sample_events, sample_distances
):
    """avg_ideological_distance_crossed is the author's bridge span.

    Proven on Gen-A: exactly 87 authors post in >=2 subreddits and exactly 87 have
    a nonzero value -- and 8 of those appear in no amplification event at all, so
    the column cannot be an average over event-table hops. A single-subreddit
    author must therefore score 0.0 no matter how many events they generate.
    """
    got = build_influence_profile(
        golden_posts, sample_diffusion, sample_events, sample_distances
    ).set_index("author")

    spans = golden_posts.groupby("author")["subreddit"].nunique()
    for author, row in got.iterrows():
        if spans.get(author, 0) < 2:
            assert row.avg_ideological_distance_crossed == 0.0, author
        else:
            assert row.avg_ideological_distance_crossed > 0.0, author


def test_distance_lookup_handles_a_noncanonical_matrix(sample_distances):
    """The frozenset lookup is defensive, not load-bearing -- and this proves it.

    build_ideological_matrix emits canonical (a < b) rows, and the span is built
    from combinations(sorted(...)), so an ordered-tuple lookup happens to fit all
    3,598 Gen-A rows too. The frozenset earns its place by not silently returning
    0.0 when a distance frame arrives in the other order.
    """
    assert (sample_distances.subreddit_a < sample_distances.subreddit_b).all()

    noncanonical = pd.DataFrame(
        {"subreddit_a": ["B"], "subreddit_b": ["A"], "ideological_distance": [0.7]}
    )
    posts = pd.DataFrame({"author": ["u1", "u1"], "subreddit": ["A", "B"]})
    diffusion = pd.DataFrame({"author": ["u1"], "narrative_id": ["n1"]})
    events = pd.DataFrame(
        {"author": pd.Series([], dtype=object),
         "amplification_percentage": pd.Series([], dtype=float)}
    )

    got = build_influence_profile(posts, diffusion, events, noncanonical)
    assert got.avg_ideological_distance_crossed.iloc[0] == pytest.approx(0.7), (
        "a non-canonical distance row silently resolved to 0.0"
    )


def test_amplification_summary_is_recomputed_from_events(sample_events):
    got = build_amplification_summary(sample_events)
    assert list(got.columns) == [
        "author", "total_relative_amplification", "amplification_events",
    ]
    grouped = sample_events.groupby("author")
    assert (
        got.set_index("author").amplification_events == grouped.size()
    ).all()
    assert np.allclose(
        got.set_index("author").total_relative_amplification,
        grouped.amplification_percentage.sum(),
    )


def test_committed_amplification_summary_is_stale():
    """Documents why we recompute: the committed file reconciles for 0 of 65."""
    summary = pd.read_csv(DATA_DIR / "author_amplification_summary.csv")
    events = pd.read_csv(DATA_DIR / "amplification_event_table.csv")
    recomputed = events.groupby("author").amplification_percentage.sum()
    overlap = summary[summary.author.isin(recomputed.index)]
    assert len(overlap) == 65
    matches = np.isclose(
        overlap.total_relative_amplification,
        recomputed.loc[overlap.author].to_numpy(),
        atol=1e-6,
    ).sum()
    assert matches == 0, f"expected 0 matches, got {matches}"
