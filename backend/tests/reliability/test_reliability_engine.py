"""
V20 Reliability Engine tests: failure taxonomy classification, stage
assignment, prevention-rule coverage, and the reliability-metrics
computation behind the dashboard. Plain assert-based -- run directly:
python tests/reliability/test_reliability_engine.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.memory.failure_memory import (
    _PATTERN_RULES,
    _PATTERN_STAGES,
    classify_failure,
    get_top_patterns,
    stage_of,
)
from app.memory.reliability_metrics import compute_reliability_metrics, render_dashboard

# Real failure strings from telemetry / build reports -> expected (stage, key).
REAL_FAILURES = {
    'error during build: src/pages/RegisterPage.jsx (3:9): "Handshake" is not exported':
        ("build", "IconNotExported"),
    "[journeycrudfailure] backend healthy but crud journey failed":
        ("integration", "JourneyCRUDFailure"),
    "Missing symbol 'SignupRequest' in app/routes/auth_routes.py (imported from app/routes/api_routes.py)":
        ("runtime", "ImportError"),
    "Missing endpoint POST /auth/login (expected in app/routes/auth_routes.py)":
        ("generation", "MissingEndpoint"),
    "sqlalchemy.exc.operationalerror: (sqlite3.OperationalError) no such table":
        ("runtime", "SQLAlchemyError"),
    "typeerror: 'str' object is not callable — AttributeError in stats handler":
        ("runtime", "AttributeError"),
    "workflow step failed: list seed via api (got 405, expected 200)":
        ("integration", "WorkflowStepFailure"),
    "Router export mismatch in app/routes/task_routes.py":
        ("build", "RouterExportMismatch"),
    "Missing File": ("generation", "MissingFile"),
    'Expected \'}\' but found isActive': ("build", "JSXNestedBrace"),
}


def test_real_failures_classify_into_taxonomy():
    for text, expected in REAL_FAILURES.items():
        got = classify_failure(text)
        assert got == expected, f"{text[:60]!r}: expected {expected}, got {got}"


def test_every_pattern_has_a_stage():
    for key in _PATTERN_RULES:
        assert stage_of(key) in ("generation", "build", "runtime", "integration", "deployment")
    # every stage-mapped key uses one of the five canonical stages
    assert set(_PATTERN_STAGES.values()) <= {"generation", "build", "runtime",
                                             "integration", "deployment"}


def test_top_recorded_patterns_have_prevention_rules():
    """The point of Reliability Memory: high-frequency patterns must carry a
    rule so build_prompt_injection can steer generation away from them.
    JourneyCRUDFailure (29x) previously had NO rule — the #1 class was
    invisible to prevention."""
    for must_have in ("JourneyCRUDFailure", "MissingEndpoint", "ConfigAttributeError",
                      "ImportError", "AttributeError", "NotNullViolationError"):
        assert must_have in _PATTERN_RULES, f"{must_have} has no prevention rule"


def test_top_patterns_injection_includes_journey_rule():
    top = get_top_patterns()
    keys = [p["key"] for p in top]
    assert "JourneyCRUDFailure" in keys, f"injection misses the #1 class: {keys}"
    assert "MissingEndpoint" in keys


def test_metrics_computation():
    gen = (
        [{"succeeded": True, "fix_count": 0, "final_score": 90.0}] * 6
        + [{"succeeded": True, "fix_count": 2, "final_score": 85.0}] * 2
        + [{"succeeded": False, "fix_count": 3, "final_score": 40.0,
            "dominant_errors": "['[journeycrudfailure] backend healthy but crud journey failed']"}] * 2
    )
    canary = [{"deploy": False, "results": [
        {"build_ok": True, "runtime_ok": True, "crud_ok": True, "browser_ok": None, "deployed": False},
        {"build_ok": False, "runtime_ok": None, "crud_ok": None, "browser_ok": None, "deployed": False},
    ]}, {"deploy": True, "results": [
        {"build_ok": True, "runtime_ok": True, "crud_ok": True, "browser_ok": True, "deployed": True},
    ]}]
    m = compute_reliability_metrics(gen, canary)
    assert m["window"] == 10
    assert m["generation_success_rate"] == 80.0
    assert m["first_try_success_rate"] == 60.0
    assert m["avg_fix_iterations"] == 1.0
    assert m["stage_rates"]["build"] == round(100 * 2 / 3, 1)
    assert m["stage_rates"]["runtime"] == 100.0
    assert m["stage_rates"]["browser"] == 100.0
    assert m["deploy_rate"] == 100.0  # only the deploy-attempted run counts
    assert m["top_failure_classes"][0] == ("JourneyCRUDFailure", 2)
    assert m["failure_stage_breakdown"] == {"integration": 2}

    dash = render_dashboard(m)
    assert "NORTH STAR" in dash and "60.0%" in dash and "JourneyCRUDFailure" in dash


def test_metrics_empty_telemetry_safe():
    m = compute_reliability_metrics([], [])
    assert m["generation_success_rate"] is None
    assert m["avg_fix_iterations"] is None
    render_dashboard(m)  # must not raise


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  PASS {name}")
            except AssertionError as e:
                failures += 1
                print(f"  FAIL {name}: {e}")
    print(f"\n{'ALL PASS' if failures == 0 else f'{failures} FAILURE(S)'}")
    sys.exit(1 if failures else 0)
