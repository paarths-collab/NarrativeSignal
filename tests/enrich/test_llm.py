"""Tests for `enrich.llm.classify` — mocks the HTTP layer, no real calls."""
from __future__ import annotations

import httpx
import pytest

from enrich import llm
from enrich.llm import ClassifyError, classify


CHOICES = ["News", "Blogs", "Advocacy", "Research", "Government", "Video", "Other"]


def _make_response(status: int, body: dict, url: str) -> httpx.Response:
    """Build a Response with a synthetic Request attached.

    httpx.Response.raise_for_status() requires a bound request; the response
    constructor lets us pass one in so tests don't blow up on 2xx responses
    when the module calls raise_for_status defensively.
    """
    return httpx.Response(
        status, json=body, request=httpx.Request("POST", url)
    )


def _groq_ok(text: str) -> httpx.Response:
    return _make_response(
        200,
        {"choices": [{"message": {"content": text}}]},
        llm.GROQ_URL,
    )


def _gemini_ok(text: str) -> httpx.Response:
    return _make_response(
        200,
        {"candidates": [{"content": {"parts": [{"text": text}]}}]},
        llm.GEMINI_URL,
    )


def _resp(status: int, url: str, body: dict | None = None) -> httpx.Response:
    return _make_response(status, body or {"error": "boom"}, url)


def test_classify_returns_a_choice(monkeypatch):
    """Groq answers with an in-vocab choice on the first try."""
    calls: list[str] = []

    def fake_post(url, **_kw):
        calls.append(url)
        return _groq_ok("News")

    monkeypatch.setattr(httpx, "post", fake_post)

    assert classify("classify example.com", CHOICES) == "News"
    assert len(calls) == 1
    assert "groq" in calls[0]


def test_falls_back_to_gemini_on_groq_429(monkeypatch):
    """Groq returns 429 -> Gemini is called in the same attempt, answer used."""
    calls: list[str] = []

    def fake_post(url, **_kw):
        calls.append(url)
        if "groq" in url:
            return _resp(429, url, {"error": "rate limited"})
        return _gemini_ok("Blogs")

    monkeypatch.setattr(httpx, "post", fake_post)

    assert classify("classify example.com", CHOICES) == "Blogs"
    # Exactly one Groq attempt, exactly one Gemini attempt, no retries.
    assert len(calls) == 2
    assert "groq" in calls[0]
    assert "generativelanguage" in calls[1]


def test_rejects_out_of_vocab_response_and_retries(monkeypatch):
    """First reply doesn't match `choices` -> a second attempt is scheduled."""
    replies = iter(["banana", "News"])
    calls: list[str] = []

    def fake_post(url, **_kw):
        calls.append(url)
        return _groq_ok(next(replies))

    monkeypatch.setattr(httpx, "post", fake_post)

    assert classify("classify example.com", CHOICES) == "News"
    # Two attempts total, both to Groq (no transient error, so no failover).
    assert len(calls) == 2
    assert all("groq" in c for c in calls)


def test_raises_classify_error_after_exhausting_retries(monkeypatch):
    """Every attempt is out-of-vocab -> ClassifyError after 4 attempts."""
    calls: list[str] = []

    def fake_post(url, **_kw):
        calls.append(url)
        return _groq_ok("banana")

    monkeypatch.setattr(httpx, "post", fake_post)

    with pytest.raises(ClassifyError):
        classify("classify example.com", CHOICES)

    # stop_after_attempt(4) => exactly four Groq calls, no Gemini (no 429/5xx).
    assert len(calls) == 4
    assert all("groq" in c for c in calls)


def test_normalize_tolerates_quotes_and_extra_prose():
    """The normalizer must handle common LLM formatting quirks."""
    assert llm._normalize('"News"', CHOICES) == "News"
    assert llm._normalize("news.", CHOICES) == "News"
    assert llm._normalize("News - established outlet", CHOICES) == "News"
    assert llm._normalize("mystery meat", CHOICES) == ""
