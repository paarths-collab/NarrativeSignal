"""Constrained-output and free-form LLM helpers, Groq primary + Gemini failover.

Public surface:
    classify(prompt: str, choices: list[str]) -> str
    generate(prompt: str, *, max_words: int = 4) -> str
    ClassifyError

Behavior:
    * Calls Groq (llama-3.3-70b-versatile) first via the OpenAI-compatible
      chat-completions endpoint.
    * On HTTP 429 or 5xx from Groq, falls back to Gemini
      (gemini-2.5-flash generateContent).
    * `classify`'s raw reply is normalized against `choices` (case-insensitive,
      whitespace/punct-stripped, single-word prefix match). If it does not
      map to any allowed choice, the attempt is treated as retryable.
    * `generate` skips the choice constraint entirely: it returns the raw
      reply cleaned of surrounding quotes/punctuation and truncated to
      `max_words` words. It still gets the same Groq->Gemini failover, but
      only retries on transient (429/5xx) errors, since there is no
      out-of-vocab concept for free-form text.
    * Both are wrapped in tenacity: 4 attempts total, exponential backoff
      1s -> 8s. On exhaustion, raises `ClassifyError`.

Non-transient HTTP errors (401, 400, ...) are surfaced as httpx exceptions
rather than swallowed, because they indicate a config bug, not a rate limit.
"""
from __future__ import annotations

import os
from typing import Sequence

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

# --- Provider endpoints / models -------------------------------------------

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"

GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/"
    "models/gemini-2.5-flash:generateContent"
)

_TIMEOUT = 30.0


# --- Exceptions ------------------------------------------------------------


class ClassifyError(RuntimeError):
    """Raised when the LLM cannot produce an in-vocab answer after retries."""


class _TransientError(RuntimeError):
    """Retryable HTTP failure (429 or 5xx) from an LLM provider."""


class _OutOfVocabError(RuntimeError):
    """LLM returned text that does not map to any allowed choice."""


# --- Provider calls --------------------------------------------------------


def _require_env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise ClassifyError(f"missing env var: {name}")
    return val


def _call_groq(prompt: str) -> str:
    """POST to Groq's OpenAI-compatible chat endpoint. Returns message text.

    Raises `_TransientError` on 429 or 5xx so the caller can fail over to
    Gemini. Any other HTTP error surfaces as an httpx exception.
    """
    key = _require_env("GROQ_API_KEY")
    resp = httpx.post(
        GROQ_URL,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        json={
            "model": GROQ_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.0,
        },
        timeout=_TIMEOUT,
    )
    if resp.status_code == 429 or resp.status_code >= 500:
        raise _TransientError(f"Groq HTTP {resp.status_code}")
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def _call_gemini(prompt: str) -> str:
    """POST to Gemini's generateContent endpoint. Returns first candidate text.

    Raises `_TransientError` on 429 or 5xx.
    """
    key = _require_env("GEMINI_API_KEY")
    resp = httpx.post(
        f"{GEMINI_URL}?key={key}",
        headers={"Content-Type": "application/json"},
        json={"contents": [{"parts": [{"text": prompt}]}]},
        timeout=_TIMEOUT,
    )
    if resp.status_code == 429 or resp.status_code >= 500:
        raise _TransientError(f"Gemini HTTP {resp.status_code}")
    resp.raise_for_status()
    return resp.json()["candidates"][0]["content"]["parts"][0]["text"]


# --- Output normalization --------------------------------------------------


def _normalize(text: str, choices: Sequence[str]) -> str:
    """Case-fold-match `text` against `choices`. Returns "" if no match.

    Tolerates surrounding whitespace/quotes/punctuation and an LLM that
    prefixes its answer with extra prose (matches on the first token).
    """
    if not text:
        return ""
    cleaned = text.strip().strip("\"'`.,!?\n\r ").lower()
    for c in choices:
        if c.lower() == cleaned:
            return c
    tokens = cleaned.split()
    if tokens:
        first = tokens[0].strip("\"'`.,!?")
        for c in choices:
            if c.lower() == first:
                return c
    return ""


# --- Retry-wrapped attempt -------------------------------------------------


@retry(
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    retry=retry_if_exception_type((_TransientError, _OutOfVocabError)),
    reraise=True,
)
def _classify_once(prompt: str, choices: Sequence[str]) -> str:
    """One retry-managed classification attempt.

    Inside a single attempt: try Groq; on transient error, fall over to
    Gemini. If either succeeds with text that normalizes to a choice, we
    return that choice. Otherwise we raise a retryable error so tenacity
    schedules another attempt.
    """
    try:
        raw = _call_groq(prompt)
    except _TransientError:
        raw = _call_gemini(prompt)  # may re-raise _TransientError -> retry
    result = _normalize(raw, choices)
    if not result:
        raise _OutOfVocabError(f"LLM returned out-of-vocab: {raw!r}")
    return result


# --- Public API ------------------------------------------------------------


def classify(prompt: str, choices: list[str]) -> str:
    """Classify `prompt` into exactly one of `choices`.

    Groq is primary, Gemini is failover on 429/5xx. Up to 4 attempts total
    with exponential backoff (1s -> 8s). Raises `ClassifyError` on exhaust.
    """
    if not choices:
        raise ClassifyError("choices is empty")
    try:
        return _classify_once(prompt, list(choices))
    except (_TransientError, _OutOfVocabError) as exc:
        raise ClassifyError(str(exc)) from exc


# --- Free-form generation ---------------------------------------------------


@retry(
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    retry=retry_if_exception_type(_TransientError),
    reraise=True,
)
def _generate_once(prompt: str) -> str:
    """One retry-managed generation attempt: Groq first, Gemini on failover.

    Unlike `_classify_once`, there is no vocabulary check, so the only
    reason to retry is a transient (429/5xx) provider error.
    """
    try:
        return _call_groq(prompt)
    except _TransientError:
        return _call_gemini(prompt)  # may re-raise _TransientError -> retry


def _truncate_words(text: str, max_words: int) -> str:
    words = text.split()
    return " ".join(words[:max_words])


def generate(prompt: str, *, max_words: int = 4) -> str:
    """Generate a free-form reply to `prompt` (no choice constraint).

    Reuses `classify`'s Groq-primary/Gemini-failover/retry machinery but
    does not constrain the output to a fixed vocabulary. The raw reply is
    stripped of surrounding whitespace/quotes/punctuation and truncated to
    at most `max_words` words.

    Raises `ClassifyError` after exhausting retries on transient errors.
    """
    try:
        raw = _generate_once(prompt)
    except _TransientError as exc:
        raise ClassifyError(str(exc)) from exc
    cleaned = raw.strip().strip("\"'`.,!?\n\r ")
    return _truncate_words(cleaned, max_words)
