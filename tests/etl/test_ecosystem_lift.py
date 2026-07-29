# tests/etl/test_ecosystem_lift.py
import numpy as np
import pandas as pd
import pytest

from etl.config import DATA_DIR
from etl.transforms.ecosystem import build_distinctive_domains, build_echo_chambers

pytestmark = pytest.mark.formula


@pytest.fixture(scope="module")
def committed():
    return pd.read_csv(DATA_DIR / "clean_top_distinctive_domains.csv")


def test_lift_arithmetic_matches_committed_rows(committed):
    """Given the committed sub_total/domain_total, lift must follow exactly."""
    global_total = committed.drop_duplicates("subreddit").sub_total.sum()
    assert global_total == 3213
    p_sub = committed["count"] / committed.sub_total
    p_glob = committed.domain_total / global_total
    assert np.allclose(p_sub, committed.p_domain_given_sub, atol=1e-12)
    assert np.allclose(p_glob, committed.p_domain_global, atol=1e-12)
    assert np.allclose(p_sub / p_glob, committed.lift, atol=1e-9)


def test_echo_score_is_mean_of_top_lifts(committed):
    exp = pd.read_csv(DATA_DIR / "echo_chamber_scores.csv")
    got = build_echo_chambers(committed)
    merged = got.merge(exp, on="subreddit", suffixes=("_got", "_exp"))
    assert len(merged) == 9
    assert np.allclose(merged.lift_got, merged.lift_exp, atol=1e-12)


def test_selection_is_top5_per_subreddit_min_count_5(golden_posts, committed):
    allowed = dict(zip(committed.domain, committed.category))
    got = build_distinctive_domains(golden_posts, allowed)
    assert (got["count"] >= 5).all()
    assert (got.groupby("subreddit").size() <= 5).all()


def test_counts_match_raw_groupby_over_8567(golden_posts, committed):
    """The one column that IS byte-reproducible: 42/42."""
    raw = golden_posts.groupby(["subreddit", "domain"]).size()
    for _, row in committed.iterrows():
        assert int(raw.get((row.subreddit, row.domain), 0)) == int(row["count"])


def test_output_column_contract(golden_posts, committed):
    allowed = dict(zip(committed.domain, committed.category))
    got = build_distinctive_domains(golden_posts, allowed)
    assert list(got.columns) == [
        "subreddit", "domain", "count", "category", "sub_total", "domain_total",
        "p_domain_given_sub", "p_domain_global", "lift",
    ]
