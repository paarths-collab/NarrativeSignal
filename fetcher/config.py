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


def _load_ua_from_env(env_var: str, product_name: str) -> str:
    """Shared: refuse generic UAs. Used by fetcher and globe."""
    ua = os.getenv(env_var, "").strip()
    if not ua:
        raise RuntimeError(
            f"{env_var} must be set (format: '{product_name}/1.0 by u/<you> (contact: <email>)')"
        )
    generic = ("python:asyncpraw", "changeme", "default")
    if any(marker in ua.lower() for marker in generic):
        raise RuntimeError(
            f"{env_var} looks generic; Nominatim/Reddit rate-limit unbranded UAs. "
            "Set a real name+contact."
        )
    return ua


def load_user_agent() -> str:
    """Read REDDIT_USER_AGENT and refuse obvious placeholders.

    Reddit rate-limits requests bearing generic default UAs. Requiring a real UA in
    the env is cheaper than debugging 429s in production.
    """
    return _load_ua_from_env("REDDIT_USER_AGENT", "narrativesignal")
