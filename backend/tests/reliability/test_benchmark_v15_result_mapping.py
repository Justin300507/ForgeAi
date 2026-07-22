"""Regression coverage for V15's benchmark-dimension mapping.

Run directly: python tests/reliability/test_benchmark_v15_result_mapping.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.benchmark.metrics import BenchmarkResult
from run_benchmark import _fill_from_v15


def test_v15_uses_dimensions_instead_of_legacy_timeline_stage_names():
    result = BenchmarkResult(prompt_file="x", name="x", difficulty="test", idea="x")
    _fill_from_v15(result, {
        "forge_score": 43.0,
        "timeline": [{"stage": "verify", "status": "passed"}],
        "dimensions": [
            {"name": "Compilation", "passed": True, "na": False, "score": 100},
            {"name": "Runtime Startup", "passed": True, "na": False, "score": 100},
            {"name": "API Functionality", "passed": True, "na": False, "score": 80},
            {"name": "Integration", "passed": False, "na": False, "score": 0},
            {"name": "Browser UX", "passed": False, "na": False, "score": 0},
        ],
    })
    assert result.compile_success is True
    assert result.runtime_success is True
    assert result.crud_success is False
    assert result.browser_success is False
    assert result.endpoint_pass_rate == 0.8


def test_v15_crud_falls_back_to_api_when_integration_is_na():
    result = BenchmarkResult(prompt_file="x", name="x", difficulty="test", idea="x")
    _fill_from_v15(result, {
        "dimensions": [
            {"name": "API Functionality", "passed": True, "na": False, "score": 100},
            {"name": "Integration", "passed": False, "na": True, "score": None},
        ],
    })
    assert result.crud_success is True


if __name__ == "__main__":
    tests = [value for name, value in globals().items() if name.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"{len(tests)}/{len(tests)} passed")
