"""Offline regression coverage for bounded Cerebras large-generation retries.

Run directly with the backend virtual environment.  All provider calls are
mocked, so no credentials, API credits, or network access are required.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.providers import ai_provider, cerebras_provider, openai_provider


def _response(content: str = "generated") -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=None,
    )


def test_large_generation_uses_one_documented_low_reasoning_request() -> None:
    create = Mock(return_value=_response())
    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )

    with patch.object(cerebras_provider, "client", fake_client):
        result = cerebras_provider.generate("large prompt", max_tokens=8_000)

    assert result == "generated"
    assert create.call_count == cerebras_provider._MAX_LARGE_GENERATION_ATTEMPTS
    assert create.call_args.kwargs["reasoning_effort"] == "low"
    assert "extra_body" not in create.call_args.kwargs


def test_large_generation_failure_stops_after_the_single_documented_attempt() -> None:
    create = Mock(side_effect=RuntimeError("Cerebras timed out"))
    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )

    with patch.object(cerebras_provider, "client", fake_client):
        try:
            cerebras_provider.generate("large prompt", max_tokens=8_000)
        except RuntimeError as error:
            assert str(error) == "Cerebras timed out"
        else:
            raise AssertionError("expected the bounded Cerebras attempts to fail")

    assert create.call_count == cerebras_provider._MAX_LARGE_GENERATION_ATTEMPTS


def test_auto_policy_uses_one_openai_mini_attempt_before_cerebras() -> None:
    openai_create = Mock(side_effect=RuntimeError("OpenAI mini unavailable"))
    fake_openai_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=openai_create))
    )
    calls: list[str] = []

    def succeed_cerebras(*_args, **_kwargs):
        calls.append("cerebras")
        return "cerebras response"

    ai_provider._provider_cooldown_until.clear()
    with (
        patch.object(openai_provider, "_get_client", return_value=fake_openai_client),
        patch.object(ai_provider, "openai_generate", openai_provider.generate),
        patch.object(ai_provider, "cerebras_generate", succeed_cerebras),
    ):
        result = ai_provider._auto_chain("test prompt", "test", 4_000, 0)

    assert result == "cerebras response"
    assert calls == ["cerebras"]
    assert openai_create.call_count == 1
    assert openai_create.call_args.kwargs["model"] == openai_provider.DEFAULT_MODEL


def test_openai_high_quality_escalation_is_explicitly_opt_in() -> None:
    create = Mock(side_effect=[RuntimeError("mini unavailable"), _response("quality response")])
    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )

    with patch.object(openai_provider, "_get_client", return_value=fake_client):
        result = openai_provider.generate("test prompt", allow_model_escalation=True)

    assert result == "quality response"
    assert [call.kwargs["model"] for call in create.call_args_list] == [
        openai_provider.DEFAULT_MODEL,
        openai_provider.FALLBACK_MODEL,
    ]


def test_cerebras_failure_enters_openai_first_fallback_chain() -> None:
    calls: list[str] = []

    def fail_cerebras(*_args, **_kwargs):
        calls.append("cerebras")
        raise RuntimeError("Cerebras timed out")

    def succeed_openai(*_args, **_kwargs):
        calls.append("openai")
        return "openai response"

    ai_provider._provider_cooldown_until.clear()
    with (
        patch.object(ai_provider, "cerebras_generate", fail_cerebras),
        patch.object(ai_provider, "openai_generate", succeed_openai),
    ):
        result = ai_provider._generate_uncached(
            "test prompt",
            provider="cerebras",
            max_tokens=8_000,
            stage="test",
        )

    assert result == "openai response"
    assert calls == ["cerebras", "openai"]


if __name__ == "__main__":
    tests = [
        test_large_generation_uses_one_documented_low_reasoning_request,
        test_large_generation_failure_stops_after_the_single_documented_attempt,
        test_auto_policy_uses_one_openai_mini_attempt_before_cerebras,
        test_openai_high_quality_escalation_is_explicitly_opt_in,
        test_cerebras_failure_enters_openai_first_fallback_chain,
    ]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"\n{len(tests)}/{len(tests)} passed")
