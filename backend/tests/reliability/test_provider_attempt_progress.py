"""Zero-network checks for real provider-attempt progress.

Run: backend\\venv\\Scripts\\python.exe backend/tests/reliability/test_provider_attempt_progress.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

_BACKEND = Path(__file__).resolve().parents[2]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


def _event_collector():
    events = []

    def collect(payload):
        events.append(payload)

    return events, collect


def test_tracked_attempt_has_only_allowed_metadata() -> None:
    from app.providers.ai_provider import _tracked, observe_provider_attempts

    events, collect = _event_collector()
    with observe_provider_attempts(collect):
        assert _tracked("openai", "not-emitted", "PROMPT_SECRET", lambda *_args, **_kwargs: "OUTPUT_SECRET", "planning") == "OUTPUT_SECRET"
    assert events == [
        {"stage": "planning", "provider": "openai", "attempt": 1, "status": "started"},
        {"stage": "planning", "provider": "openai", "attempt": 1, "status": "succeeded"},
    ]
    assert "PROMPT_SECRET" not in repr(events) and "OUTPUT_SECRET" not in repr(events)


def test_openai_failure_then_retry_success_reports_actual_truth() -> None:
    """Cerebras/Gemini/Groq were removed as fallbacks (2026-07-30, no usable
    credits/keys on Railway) -- a failed OpenAI attempt now retries OpenAI
    itself, reported as a second 'openai' attempt rather than a different
    provider."""
    import app.providers.ai_provider as provider

    events, collect = _event_collector()
    original_cooldowns = dict(provider._provider_cooldown_until)
    provider._provider_cooldown_until.clear()
    openai_calls = [RuntimeError("unavailable"), "openai retry response"]

    def flaky_openai(*_args, **_kwargs):
        result = openai_calls.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    try:
        with (
            patch.object(provider, "openai_generate", side_effect=flaky_openai),
            patch.object(provider, "_note_provider_result", return_value=None),
            patch.object(provider, "time") as mock_time,
            provider.observe_provider_attempts(collect),
        ):
            mock_time.time.return_value = 1.0
            assert provider._generate_uncached("PROMPT_SECRET", provider="openai", stage="planning") == "openai retry response"
    finally:
        provider._provider_cooldown_until.clear()
        provider._provider_cooldown_until.update(original_cooldowns)
    assert events == [
        {"stage": "planning", "provider": "openai", "attempt": 1, "status": "started"},
        {"stage": "planning", "provider": "openai", "attempt": 1, "status": "failed"},
        {"stage": "planning", "provider": "openai", "attempt": 2, "status": "started"},
        {"stage": "planning", "provider": "openai", "attempt": 2, "status": "succeeded"},
    ]
    assert "PROMPT_SECRET" not in repr(events)


def test_cache_hit_does_not_emit_provider_attempt() -> None:
    import app.providers.ai_provider as provider

    events, collect = _event_collector()
    with (
        patch("app.utils.llm_cache.get_cached", return_value={"response": "cached response"}),
        patch.object(provider, "_generate_uncached", side_effect=AssertionError("cache must avoid physical call")),
        provider.observe_provider_attempts(collect),
    ):
        assert provider.generate_content("PROMPT_SECRET", stage="planning") == "cached response"
    assert events == []


def main() -> None:
    tests = [
        test_tracked_attempt_has_only_allowed_metadata,
        test_openai_failure_then_cerebras_success_reports_actual_truth,
        test_cache_hit_does_not_emit_provider_attempt,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"{len(tests)}/{len(tests)} provider attempt progress tests passed")


if __name__ == "__main__":
    main()
