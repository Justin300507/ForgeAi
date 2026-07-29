"""Offline regression checks for redacted provider failure telemetry."""
from __future__ import annotations

import os
import sys
import json
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.providers import ai_provider
from app.utils import cost_tracker


def _assert_failure(
    failure: dict,
    *,
    provider: str,
    model: str,
    stage: str,
    error_class: str,
    timeout: bool,
) -> None:
    assert failure["provider"] == provider
    assert failure["model"] == model
    assert failure["stage"] == stage
    assert failure["error_class"] == error_class
    assert failure["timeout"] is timeout
    assert "prompt" not in failure
    assert "sk-test-secret" not in json.dumps(failure)


def test_openai_failure_records_one_redacted_entry_then_retries_openai() -> None:
    """Cerebras/Gemini/Groq were removed as fallbacks (2026-07-30, no usable
    credits/keys on Railway) -- a failed OpenAI attempt retries OpenAI
    itself rather than falling through to another provider."""
    calls: list[str] = []
    responses = [TimeoutError("request timed out authorization=sk-test-secret"), "openai retry response"]

    def flaky_openai(*_args, **_kwargs):
        calls.append("openai")
        result = responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    cost_tracker.reset_session()
    ai_provider._provider_cooldown_until.clear()
    try:
        with (
            patch.dict(os.environ, {"FORGE_LLM_CACHE": "0"}, clear=False),
            patch.object(ai_provider, "openai_generate", flaky_openai),
            patch.object(ai_provider.time, "sleep", lambda *_a, **_k: None),
        ):
            result = ai_provider.generate_content(
                "private prompt that must not reach telemetry",
                provider="openai",
                stage="architect",
            )

        assert result == "openai retry response"
        assert calls == ["openai", "openai"]
        failures = cost_tracker.get_session_failures()
        assert len(failures) == 1
        _assert_failure(
            failures[0],
            provider="openai",
            model=ai_provider.openai_default_model,
            stage="architect",
            error_class="timeout",
            timeout=True,
        )
    finally:
        cost_tracker.reset_session()


def test_explicit_cerebras_request_never_attempts_cerebras_or_records_a_failure() -> None:
    """No credits configured for Cerebras -- an explicit request for it
    must go straight to OpenAI without attempting Cerebras, so no failure
    telemetry is recorded for it at all."""
    def succeed_openai(*_args, **_kwargs):
        return "openai response"

    cost_tracker.reset_session()
    ai_provider._provider_cooldown_until.clear()
    try:
        with (
            patch.dict(os.environ, {"FORGE_LLM_CACHE": "0"}, clear=False),
            patch.object(ai_provider, "openai_generate", succeed_openai),
        ):
            result = ai_provider.generate_content(
                "private prompt that must not reach telemetry",
                provider="cerebras",
                stage="repair",
            )

        assert result == "openai response"
        assert cost_tracker.get_session_failures() == []
    finally:
        cost_tracker.reset_session()


def test_exception_text_that_echoes_prompt_is_never_persisted() -> None:
    marker = "UNIQUE_PROMPT_MARKER_do_not_persist"

    cost_tracker.reset_session()
    try:
        cost_tracker.record_llm_failure(
            "architect",
            "openai",
            "gpt-4o-mini",
            0.01,
            RuntimeError(f"provider echoed {marker}"),
        )
        failures = cost_tracker.get_session_failures()
        assert len(failures) == 1
        assert marker not in json.dumps(failures[0])
        assert failures[0]["error_class"] == "provider_error"
        assert failures[0]["error_summary"] == "provider request failed"
    finally:
        cost_tracker.reset_session()


if __name__ == "__main__":
    tests = [
        test_openai_failure_records_one_redacted_entry_then_retries_openai,
        test_explicit_cerebras_request_never_attempts_cerebras_or_records_a_failure,
        test_exception_text_that_echoes_prompt_is_never_persisted,
    ]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"\n{len(tests)}/{len(tests)} passed")
