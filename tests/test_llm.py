"""app.llm.anthropic_client: the one place scripts build an Anthropic client from."""

from __future__ import annotations

import pytest

from app import llm


def test_raises_a_clear_error_when_the_key_is_unset(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        llm.anthropic_client()


def test_returns_a_client_when_the_key_is_set(monkeypatch):
    """Positive control. The key value is never echoed — only its presence matters."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
    client = llm.anthropic_client()
    assert client.__class__.__name__ == "Anthropic"
    assert client.timeout == llm.DEFAULT_TIMEOUT
    assert client.max_retries == llm.DEFAULT_MAX_RETRIES


def test_timeout_override_is_honoured(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
    assert llm.anthropic_client(timeout=12.5).timeout == 12.5
