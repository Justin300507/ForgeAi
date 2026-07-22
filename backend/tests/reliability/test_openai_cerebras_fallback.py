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


def test_explicit_openai_failure_goes_directly_to_cerebras() -> None:
    calls: list[str] = []

    def fail_openai(*_args, **_kwargs):
        calls.append("openai")
        raise RuntimeError("temporary OpenAI failure")

    def succeed_cerebras(*_args, **_kwargs):
        calls.append("cerebras")
        return "cerebras response"

    ai_provider._provider_cooldown_until.clear()
    with (
        patch.object(ai_provider, "openai_generate", fail_openai),
        patch.object(ai_provider, "cerebras_generate", succeed_cerebras),
    ):
        result = ai_provider._generate_uncached(
            "test prompt",
            provider="openai",
            stage="test",
        )

    assert result == "cerebras response"
    assert calls == ["openai", "cerebras"]


def test_auto_chain_uses_openai_before_cerebras() -> None:
    calls: list[str] = []

    def fail_openai(*_args, **_kwargs):
        calls.append("openai")
        raise RuntimeError("temporary OpenAI failure")

    def succeed_cerebras(*_args, **_kwargs):
        calls.append("cerebras")
        return "cerebras response"

    ai_provider._provider_cooldown_until.clear()
    with (
        patch.object(ai_provider, "openai_generate", fail_openai),
        patch.object(ai_provider, "cerebras_generate", succeed_cerebras),
    ):
        result = ai_provider._auto_chain("test prompt", "test", 4000, 0)

    assert result == "cerebras response"
    assert calls == ["openai", "cerebras"]


if __name__ == "__main__":
    tests = [
        test_openai_client_has_one_bounded_attempt_before_provider_fallback,
        test_explicit_openai_failure_goes_directly_to_cerebras,
        test_auto_chain_uses_openai_before_cerebras,
    ]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"\n{len(tests)}/{len(tests)} passed")
