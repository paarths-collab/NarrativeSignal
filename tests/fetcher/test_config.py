import os

import pytest

from fetcher.config import SUBREDDITS, load_user_agent, listing_budget


def test_subreddits_are_the_ten_from_etl():
    from etl.config import SUBREDDITS as etl_subs
    assert SUBREDDITS == etl_subs
    assert len(SUBREDDITS) == 10


def test_user_agent_reads_env(monkeypatch):
    monkeypatch.setenv("REDDIT_USER_AGENT", "narrativesignal/1.0 by u/x (contact: y@z)")
    assert load_user_agent() == "narrativesignal/1.0 by u/x (contact: y@z)"


def test_user_agent_missing_env_raises(monkeypatch):
    monkeypatch.delenv("REDDIT_USER_AGENT", raising=False)
    with pytest.raises(RuntimeError, match="REDDIT_USER_AGENT"):
        load_user_agent()


def test_user_agent_default_is_rejected(monkeypatch):
    """Reddit rate-limits generic UAs; force a real one."""
    monkeypatch.setenv("REDDIT_USER_AGENT", "python:asyncpraw")
    with pytest.raises(RuntimeError, match="looks generic"):
        load_user_agent()


def test_listing_budget_is_conservative():
    """Two endpoints * 100 posts * 10 subs = 2000 requests worst case."""
    budget = listing_budget()
    assert budget.per_sub_new_limit == 100
    assert budget.per_sub_top_limit == 100
