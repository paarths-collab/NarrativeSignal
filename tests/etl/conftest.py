"""Golden-master fixtures: the committed CSVs are the known-good oracle.

These fixtures are session-scoped: every test in the run shares the same
DataFrame objects. Treat them as read-only — copy before mutating
(`df.copy()`, avoid `inplace=True` or column assignment on the fixture
itself). Mutating a shared fixture corrupts it for every later test and
produces order-dependent failures.
"""
import pandas as pd
import pytest

from etl.config import DATA_DIR


def _read(name: str, **kwargs) -> pd.DataFrame:
    return pd.read_csv(DATA_DIR / name, **kwargs)


@pytest.fixture(scope="session")
def golden_clean_posts() -> pd.DataFrame:
    """8,687 rows. The root of the cascade."""
    return _read("clean_posts.csv", parse_dates=["created_datetime"])


@pytest.fixture(scope="session")
def golden_clusters() -> pd.DataFrame:
    """8,567 rows, with duplicate_cluster_id."""
    return _read("clean_with_clusters_v2.csv", parse_dates=["created_datetime"])


@pytest.fixture(scope="session")
def golden_posts(golden_clusters) -> pd.DataFrame:
    """The 8,567 set without the cluster column."""
    return golden_clusters.drop(columns=["duplicate_cluster_id"])
