"""
Verifies the Forensic Bundle writer: monotonic failure IDs, a generic
schema any failure class can populate, auth redaction, and that the
returned {failure_id, bundle_path} actually resolves to a written file.
Run directly: python tests/reliability/test_forensic_bundle.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.memory import forensic_bundle

import tempfile
from pathlib import Path

# Redirect the module's storage to an isolated temp dir so the test run never
# writes into real backend/failure_memory/ or advances the real FR-NNNNNN seq.
# write_bundle()/next_failure_id() read these as live module attributes.
_TEST_MEM_DIR = Path(tempfile.mkdtemp(prefix="forensic_bundle_test_"))
forensic_bundle.BUNDLE_DIR = _TEST_MEM_DIR / "bundles"
forensic_bundle._SEQ_PATH = _TEST_MEM_DIR / "failure_id_seq.json"


def test_next_failure_id_format_and_monotonic():
    a = forensic_bundle.next_failure_id()
    b = forensic_bundle.next_failure_id()
    assert a.startswith("FR-") and len(a) == 9
    assert b.startswith("FR-") and len(b) == 9
    assert int(b.split("-")[1]) == int(a.split("-")[1]) + 1


def test_write_bundle_returns_failure_id_and_path():
    result = forensic_bundle.write_bundle(
        project="unit_test_project",
        stage="runtime",
        failure_class="JourneyCRUDFailure",
        step="Create entity",
        provider="gemini",
        request={"method": "POST", "url": "http://x/items", "json": {"a": 1}},
        response={"status_code": 422, "body": {"detail": "bad"}},
    )
    assert result["failure_id"].startswith("FR-")
    assert result["bundle_path"].startswith("failure_memory/bundles/")
    assert result["bundle_path"].endswith(".json")


def test_write_bundle_file_has_generic_schema():
    result = forensic_bundle.write_bundle(
        project="unit_test_project",
        stage="deployment",
        failure_class="DeployFailure",
        step=None,
        request=None,
        response=None,
        stderr="some traceback",
    )
    # Storage is redirected to the temp dir, so resolve the returned basename
    # against _TEST_MEM_DIR rather than the real backend/failure_memory/.
    full_path = _TEST_MEM_DIR / "bundles" / os.path.basename(result["bundle_path"])
    with open(full_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert data["bundle_version"] == 1
    assert data["failure_id"] == result["failure_id"]
    assert data["failure"] == {"stage": "deployment", "class": "DeployFailure", "step": None}
    assert data["project"] == "unit_test_project"
    assert data["request"] is None
    assert data["response"] is None
    assert data["stderr"] == "some traceback"
    # Generic metadata fields must exist even when the caller doesn't supply them
    assert "generation" in data
    assert set(data["generation"].keys()) >= {"category", "style", "layout", "design_fingerprint_id"}
    assert "commit_sha" in data
    assert "forgeai_version" in data
    assert "pipeline_version" in data
    # Reserved for V20.3 (Browser Evidence) — must exist, all null, from day one
    # so no future bundle needs a schema migration.
    assert data["artifacts"] == {
        "screenshot": None, "console_log": None,
        "network_log": None, "playwright_trace": None,
    }


def test_write_bundle_never_stores_raw_auth_header():
    result = forensic_bundle.write_bundle(
        project="unit_test_project",
        stage="runtime",
        failure_class="JourneyCRUDFailure",
        request={"method": "POST", "url": "http://x/items",
                 "json": {"a": 1},
                 "headers": {"Authorization": "Bearer super-secret-token-abc123"},
                 "has_auth": True},
        response={"status_code": 401, "body": {"token": "Bearer another-secret-xyz789"}},
    )
    full_path = _TEST_MEM_DIR / "bundles" / os.path.basename(result["bundle_path"])
    raw_text = open(full_path, "r", encoding="utf-8").read()
    assert "super-secret-token-abc123" not in raw_text
    assert "another-secret-xyz789" not in raw_text
    assert "Authorization" not in raw_text


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
