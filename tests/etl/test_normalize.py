import json
from pathlib import Path

import pandas as pd
import pytest

from etl.config import CLEAN_POSTS_COLUMNS
from etl.normalize import normalize_listing

FIXTURE = Path(__file__).parent / "fixtures" / "sample_listing.jsonl"


@pytest.fixture(scope="module")
def sample() -> pd.DataFrame:
    return normalize_listing(FIXTURE)


def test_column_order_matches_contract(sample):
    assert list(sample.columns) == CLEAN_POSTS_COLUMNS


def test_automoderator_is_dropped(sample):
    assert "AutoModerator" not in set(sample.author)


def test_deleted_authors_are_preserved(sample):
    raw = [json.loads(l)["data"] for l in FIXTURE.read_text(encoding="utf-8").splitlines()]
    assert any(r["author"] == "[deleted]" for r in raw), "fixture must cover this case"
    assert "[deleted]" in set(sample.author)


def test_domain_is_copied_not_derived(sample):
    """Self-posts carry 'self.<Subreddit>', which urlparse could never produce."""
    selfies = sample[sample.domain.str.startswith("self.", na=False)]
    assert len(selfies) > 0
    assert selfies.url.str.contains("reddit.com").all()


def test_created_datetime_is_utc_seconds(sample):
    raw = {
        json.loads(l)["data"]["id"]: json.loads(l)["data"]["created_utc"]
        for l in FIXTURE.read_text(encoding="utf-8").splitlines()
    }
    row = sample.iloc[0]
    expected = pd.to_datetime(raw[row.id], unit="s")
    assert row.created_datetime == expected
    assert row.created_datetime.microsecond == 0


def test_golden_master_full_corpus(golden_clean_posts):
    """Skips in CI; runs locally where the 45MB raw dump is present."""
    raw = Path("_original_pipeline/data.jsonl")
    if not raw.exists():
        pytest.skip("raw dump not available")
    got = normalize_listing(raw).reset_index(drop=True)
    exp = golden_clean_posts.reset_index(drop=True)
    assert len(got) == 8687
    pd.testing.assert_frame_equal(got, exp, check_dtype=False)
