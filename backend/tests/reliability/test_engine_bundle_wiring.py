"""
Verifies engine.py writes a Forensic Bundle for a failed CRUD journey step
and attaches {failure_id, bundle_path} to the diagnostic's metadata, using
a synthetic journey dict (no real HTTP server needed).
Run directly: python tests/reliability/test_engine_bundle_wiring.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.verification.engine import _write_journey_bundle
from app.memory import forensic_bundle

# Redirect bundle storage to an isolated temp dir so this test never writes
# into real backend/failure_memory/ or advances the real FR-NNNNNN seq.
# _write_journey_bundle -> write_bundle reads these as live module attributes.
_TEST_MEM_DIR = Path(tempfile.mkdtemp(prefix="engine_bundle_wiring_test_"))
forensic_bundle.BUNDLE_DIR = _TEST_MEM_DIR / "bundles"
forensic_bundle._SEQ_PATH = _TEST_MEM_DIR / "failure_id_seq.json"


def _ctx(project_name="demo_project", provider="gemini"):
    return SimpleNamespace(project_name=project_name, current_provider=provider)


def test_returns_none_when_no_step_has_evidence():
    journey = {"steps": [{"name": "Register", "passed": False, "detail": "404"}]}
    assert _write_journey_bundle(_ctx(), journey) is None


def test_returns_none_when_journey_succeeded():
    journey = {"steps": [{"name": "Register", "passed": True, "detail": "201",
                           "request": {"method": "POST"}, "response": {"status_code": 201}}]}
    assert _write_journey_bundle(_ctx(), journey) is None


def test_writes_bundle_for_failed_step_with_evidence():
    journey = {
        "steps": [
            {"name": "Register", "passed": True, "detail": "201"},
            {"name": "Create entity", "passed": False, "detail": "422",
             "request": {"method": "POST", "url": "http://x/items", "json": {"a": 1}},
             "response": {"status_code": 422, "body": {"detail": "bad"}}},
        ]
    }
    result = _write_journey_bundle(_ctx(project_name="demo_project", provider="groq"), journey)
    assert result is not None
    assert result["failure_id"].startswith("FR-")

    # Storage is redirected to the temp dir, so resolve the returned basename
    # against _TEST_MEM_DIR rather than the real backend/failure_memory/.
    full_path = _TEST_MEM_DIR / "bundles" / os.path.basename(result["bundle_path"])
    with open(full_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert data["project"] == "demo_project"
    assert data["provider"] == "groq"
    assert data["failure"]["class"] == "JourneyCRUDFailure"
    assert data["failure"]["step"] == "Create entity"
    assert data["request"]["json"] == {"a": 1}
    assert data["response"]["status_code"] == 422


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
