import pandas as pd

from etl.config import DATA_DIR
from etl.transforms.volume import build_daily_volume, reindex_complete_days


def test_reproduces_committed_artifact(golden_posts):
    got = build_daily_volume(golden_posts)
    exp = pd.read_csv(DATA_DIR / "daily_volume_v2.csv")
    assert len(got) == 201
    assert got.post_count.sum() == 8567
    pd.testing.assert_frame_equal(
        got.reset_index(drop=True), exp.reset_index(drop=True), check_dtype=False
    )


def test_built_from_the_8567_set_not_clean_posts(golden_clean_posts, golden_posts):
    """Using the wrong cascade level silently changes 71 of 201 days."""
    assert build_daily_volume(golden_posts).post_count.sum() == 8567
    assert build_daily_volume(golden_clean_posts).post_count.sum() == 8687


def test_empty_days_are_absent_by_default(golden_posts):
    got = build_daily_volume(golden_posts)
    span = (pd.to_datetime(got.created_datetime).max()
            - pd.to_datetime(got.created_datetime).min()).days + 1
    assert span == 211 and len(got) == 201


def test_reindex_fills_gaps_with_zero(golden_posts):
    """Downstream z-scores use a row-based window, so gaps must be fillable."""
    filled = reindex_complete_days(build_daily_volume(golden_posts))
    assert len(filled) == 211
    assert (filled.post_count == 0).sum() == 10
    assert filled.post_count.sum() == 8567
