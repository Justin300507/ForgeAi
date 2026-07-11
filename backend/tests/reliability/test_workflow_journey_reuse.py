"""
Verifies the playwright_workflow.py <-> user_journey_runner.py reliability
fix: the workflow-test harness reuses an already-run CRUD journey instead
of re-deriving the CRUD entity and re-running it with a second, divergent
implementation.

Root cause this fixes (see Experiment log): playwright_workflow.py's OLD
`_detect_entity()` picked the first non-auth/users/me endpoint segment with
NO CRUD-capability check, so on apps whose architecture lists a POST-only
`/seed` endpoint before the real resource, it tested CRUD against `/seed`
and got a guaranteed 405 on the GET-list check. Its OLD login step body was
hardcoded to `{"username":..., "password":...}` with no OpenAPI
introspection, so on apps needing `email` it silently "passed" on a 422
without ever capturing a token, and every subsequent authenticated call
got a guaranteed 401. Both were 100%-reproducing false negatives that fed
the Integration score dimension and the repair loop.

Run directly: python tests/reliability/test_workflow_journey_reuse.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.runtime.playwright_workflow import _journey_to_steps, WorkflowResult


def test_journey_to_steps_splits_passed_and_failed():
    journey = {
        "steps": [
            {"name": "Register", "passed": True, "detail": "201 @ register"},
            {"name": "Login", "passed": True, "detail": "200 @ login"},
            {"name": "Create entity", "passed": False,
             "detail": "401 (auth required, no valid token)"},
            {"name": "List entities", "passed": False, "detail": "401"},
        ]
    }
    passed, failed = _journey_to_steps(journey)
    assert passed == ["Register: 201 @ register", "Login: 200 @ login"]
    assert failed == ["Create entity: 401 (auth required, no valid token)",
                       "List entities: 401"]


def test_journey_to_steps_omits_detail_when_blank():
    journey = {"steps": [{"name": "Logout", "passed": True, "detail": ""}]}
    passed, failed = _journey_to_steps(journey)
    assert passed == ["Logout"]
    assert failed == []


def test_journey_to_steps_empty_journey_yields_no_steps():
    assert _journey_to_steps({}) == ([], [])
    assert _journey_to_steps({"steps": []}) == ([], [])


def test_workflow_result_separates_nav_failures_from_journey_failures():
    # This is the contract engine.py's _run_workflow_tests relies on: only
    # nav_steps_failed should generate a NEW diagnostic, since journey-step
    # failures are already diagnosed by Stage 3's JourneyCRUDFailure check.
    wf = WorkflowResult(
        success=False,
        steps_passed=["Register: 201"],
        steps_failed=["Create entity: 401", "Load login page: timeout"],
        nav_steps_failed=["Load login page: timeout"],
    )
    assert "Create entity: 401" in wf.steps_failed
    assert "Create entity: 401" not in wf.nav_steps_failed
    assert wf.nav_steps_failed == ["Load login page: timeout"]


def test_no_hardcoded_entity_detection_remains():
    """
    Guards against regression back to the buggy standalone entity-detection
    path: playwright_workflow.py must not define its own _detect_entity
    (that logic belongs solely to user_journey_runner._detect_crud_entity,
    which is CRUD-capability-aware).
    """
    src_path = os.path.join(os.path.dirname(__file__), "..", "..",
                             "app", "runtime", "playwright_workflow.py")
    with open(src_path, "r", encoding="utf-8") as f:
        src = f.read()
    assert "_detect_entity" not in src
    assert "def _build_workflow_steps" not in src


if __name__ == "__main__":
    import traceback
    tests = [obj for name, obj in list(globals().items()) if name.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS: {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL: {t.__name__}: {e}")
        except Exception:
            failed += 1
            print(f"ERROR: {t.__name__}:")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    raise SystemExit(1 if failed else 0)
