"""asyncpraw client factory and retry wrapper."""
import os
from typing import Awaitable, Callable, TypeVar

import asyncpraw
from asyncprawcore.exceptions import Forbidden, NotFound, ResponseException
from tenacity import (
    retry, retry_if_exception, stop_after_attempt, wait_exponential,
)

from fetcher.config import load_user_agent

T = TypeVar("T")


def _is_transient(exc: BaseException) -> bool:
    """Retry only on server-side transient errors.

    - Forbidden (403): auth or private subreddit — will not fix itself.
    - NotFound (404): banned/deleted subreddit — will not fix itself.
    - Anything else HTTP-shaped: treat as transient.
    """
    if isinstance(exc, (Forbidden, NotFound)):
        return False
    return isinstance(exc, ResponseException)


async def fetch_with_retry(
    call: Callable[[], Awaitable[T]],
    *,
    attempts: int = 5,
    min_wait: float = 1.0,
    max_wait: float = 30.0,
) -> T:
    """Run `call()`, retrying transient errors with exponential backoff."""
    @retry(
        stop=stop_after_attempt(attempts),
        wait=wait_exponential(multiplier=min_wait, max=max_wait),
        retry=retry_if_exception(_is_transient),
        reraise=True,
    )
    async def _run():
        return await call()

    return await _run()


def make_reddit_client() -> asyncpraw.Reddit:
    """Read secrets from env and construct the async client.

    Raises RuntimeError with a clear message if any required var is missing.
    """
    missing = [
        v for v in ("REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET")
        if not os.environ.get(v)
    ]
    if missing:
        raise RuntimeError(f"missing env vars: {', '.join(missing)}")
    return asyncpraw.Reddit(
        client_id=os.environ["REDDIT_CLIENT_ID"],
        client_secret=os.environ["REDDIT_CLIENT_SECRET"],
        user_agent=load_user_agent(),
    )
