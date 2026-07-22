"""Regression checks for honest, model-aware generation cost accounting."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.utils import cost_tracker


def test_gpt4o_mini_uses_separate_input_and_output_prices() -> None:
    cost_tracker.reset_session()
    try:
        cost_tracker.record_llm_call(
            "generation", "openai", "gpt-4o-mini", 1_000_000, 1_000_000, 0.1,
        )
        totals = cost_tracker.get_run_totals()
        assert totals["total_tokens"] == 2_000_000
        # $0.15/M input + $0.60/M output, per OpenAI's standard API pricing.
        assert round(totals["cost_usd"], 6) == 0.75
    finally:
        cost_tracker.reset_session()


if __name__ == "__main__":
    test_gpt4o_mini_uses_separate_input_and_output_prices()
    print("1/1 cost accounting test passed")
