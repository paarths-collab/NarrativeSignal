import asyncio

import pytest
from asyncprawcore.exceptions import Forbidden, NotFound, ServerError

from fetcher.client import fetch_with_retry


class Boom:
    def __init__(self, *errors):
        self._errors = list(errors)
        self.calls = 0

    async def do(self):
        self.calls += 1
        if self._errors:
            raise self._errors.pop(0)
        return "ok"


def test_retries_transient_server_errors():
    boom = Boom(ServerError(_mock_response(500)), ServerError(_mock_response(503)))
    result = asyncio.run(fetch_with_retry(boom.do, attempts=3, min_wait=0.01))
    assert result == "ok"
    assert boom.calls == 3


def test_does_not_retry_forbidden():
    boom = Boom(Forbidden(_mock_response(403)))
    with pytest.raises(Forbidden):
        asyncio.run(fetch_with_retry(boom.do, attempts=3, min_wait=0.01))
    assert boom.calls == 1


def test_does_not_retry_not_found():
    boom = Boom(NotFound(_mock_response(404)))
    with pytest.raises(NotFound):
        asyncio.run(fetch_with_retry(boom.do, attempts=3, min_wait=0.01))
    assert boom.calls == 1


def test_gives_up_after_attempts_and_reraises():
    err = ServerError(_mock_response(500))
    boom = Boom(err, err, err)
    with pytest.raises(ServerError):
        asyncio.run(fetch_with_retry(boom.do, attempts=3, min_wait=0.01))
    assert boom.calls == 3


def _mock_response(status: int):
    class R:
        def __init__(self, s): self.status = s
    return R(status)
