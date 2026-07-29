"""Fetcher configuration (secrets from env, subreddit list re-exported from etl)."""
import os
from dataclasses import dataclass

from etl.config import SUBREDDITS  # single source of truth  # noqa: F401


@dataclass(frozen=True)
class ListingBudget:
    per_sub_new_limit: int = 100
    per_sub_top_limit: int = 100


def listing_budget() -> ListingBudget:
    return ListingBudget()


def load_user_agent() -> str:
    """Read REDDIT_USER_AGENT and refuse obvious placeholders.

    Reddit rate-limits requests bearing generic default UAs. Requiring a real UA in
    the env is cheaper than debugging 429s in production.
    """
    ua = os.environ.get("REDDIT_USER_AGENT", "").strip()
    if not ua:
        raise RuntimeError(
            "REDDIT_USER_AGENT is required. See .env.example for the expected format."
        )
    if any(marker in ua.lower() for marker in ("python:asyncpraw", "changeme", "default")):
        raise RuntimeError(
            f"REDDIT_USER_AGENT={ua!r} looks generic. Use "
            '"narrativesignal/1.0 by u/<name> (contact: <email>)".'
        )
    return ua
