"""Shared fixtures for `enrich` tests.

Two autouse fixtures:
    - stub env vars so `_require_env` does not blow up
    - disable tenacity sleeps so the retry-exhaust test doesn't take 15s
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _stub_env(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-groq-key")
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Neutralize tenacity's between-attempt sleep in the classifier."""
    from enrich import llm

    monkeypatch.setattr(
        llm._classify_once.retry, "sleep", lambda _seconds: None
    )
