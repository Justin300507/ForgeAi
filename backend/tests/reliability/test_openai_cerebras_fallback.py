"""Regression checks for the OpenAI-first provider policy.

Run directly with the backend virtual environment; no provider credentials or
network requests are used.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.providers import ai_provider, openai_provider


def test_openai_client_has_one_bounded_attempt_before_provider_fallback() -> None:
    """Client construction is local/mocked: this test never contacts OpenAI."""
    constructor = Mock()
    previous_client = openai_provider._client
    try:
        with (
            patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}),
            patch("openai.OpenAI", constructor),
        ):
            openai_provider._client = None
            openai_provider._get_client()

        assert constructor.call_count == 1
        assert constructor.call_args.kwargs == {
            "api_key": "test-key",
            "timeout": openai_provider._REQUEST_TIMEOUT_SECONDS,
            "max_retries": 0,
        }
        assert openai_provider._REQUEST_TIMEOUT_SECONDS == 45.0
    finally:
        openai_provider._client = previous_client


def test_explicit_openai_failure_retries_openai_itself() -> None:
    """Cerebras/Gemini/Groq were removed as fallbacks (2026-07-30, no usable
    credits/keys on Railway for any of them) -- a transient OpenAI failure
    now retries OpenAI directly instead of cascading to dead providers."""
    calls: list[str] = []

    def flaky_openai(*_args, **_kwargs):
        calls.append("openai")
        if len(calls) < 2:
            raise RuntimeError("temporary OpenAI failure")
        return "openai response"

    ai_provider._provider_cooldown_until.clear()
    with (
        patch.object(ai_provider, "openai_generate", flaky_openai),
        patch.object(ai_provider.time, "sleep", lambda *_a, **_k: None),
    ):
        result = ai_provider._generate_uncached(
            "test prompt",
            provider="openai",
            stage="test",
        )

    assert result == "openai response"
    assert calls == ["openai", "openai"]


def test_auto_chain_retries_openai_and_raises_if_all_attempts_fail() -> None:
    calls: list[str] = []

    def fail_openai(*_args, **_kwargs):
        calls.append("openai")
        raise RuntimeError("temporary OpenAI failure")

    ai_provider._provider_cooldown_until.clear()
    with (
        patch.object(ai_provider, "openai_generate", fail_openai),
        patch.object(ai_provider.time, "sleep", lambda *_a, **_k: None),
    ):
        try:
            ai_provider._auto_chain("test prompt", "test", 4000, 0)
            assert False, "expected RuntimeError"
        except RuntimeError as e:
            assert "OpenAI failed after" in str(e)

    assert calls == ["openai"] * ai_provider._OPENAI_RETRY_ATTEMPTS


def test_explicit_cerebras_request_uses_openai_instead() -> None:
    """No credits configured for Cerebras -- an explicit request for it
    should go straight to OpenAI rather than attempting it at all."""
    calls: list[str] = []

    def succeed_openai(*_args, **_kwargs):
        calls.append("openai")
        return "openai response"

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("cerebras must never be attempted")

    ai_provider._provider_cooldown_until.clear()
    with (
        patch.object(ai_provider, "openai_generate", succeed_openai),
    ):
        result = ai_provider._generate_uncached("test prompt", provider="cerebras", stage="test")

    assert result == "openai response"
    assert calls == ["openai"]


if __name__ == "__main__":
    tests = [
        test_openai_client_has_one_bounded_attempt_before_provider_fallback,
        test_explicit_openai_failure_retries_openai_itself,
        test_auto_chain_retries_openai_and_raises_if_all_attempts_fail,
        test_explicit_cerebras_request_uses_openai_instead,
    ]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"\n{len(tests)}/{len(tests)} passed")
