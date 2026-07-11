"""
Verifies GenerationRecord carries bundle_refs (defaulting to empty,
round-tripping through the same asdict()/json path generation_log.jsonl
already uses), and that pipeline.py's generation_log write extracts
bundle_ref from diagnostic metadata into it.
Run directly: python tests/reliability/test_generation_record_bundle_refs.py
"""
import json
import os
import sys
from dataclasses import asdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.knowledge.failure_db import GenerationRecord


def test_generation_record_defaults_bundle_refs_to_empty_list():
    rec = GenerationRecord(
        idea="x", attempt_number=1, final_score=90.0, succeeded=True,
        fix_count=0, dominant_errors=[], architecture_hash="abc123",
    )
    assert rec.bundle_refs == []


def test_generation_record_round_trips_bundle_refs_through_json():
    rec = GenerationRecord(
        idea="x", attempt_number=1, final_score=40.0, succeeded=False,
        fix_count=2, dominant_errors=["[JourneyCRUDFailure] ..."],
        architecture_hash="abc123",
        bundle_refs=[{"failure_id": "FR-000001", "bundle_path": "failure_memory/bundles/x.json"}],
    )
    line = json.dumps(asdict(rec))
    restored = GenerationRecord(**json.loads(line))
    assert restored.bundle_refs == [{"failure_id": "FR-000001",
                                      "bundle_path": "failure_memory/bundles/x.json"}]


def test_pipeline_extracts_bundle_ref_from_diagnostic_metadata():
    src_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "app", "core", "pipeline.py"
    )
    with open(src_path, "r", encoding="utf-8") as f:
        src = f.read()
    block_start = src.index("generation_log.record(GenerationRecord(")
    block = src[block_start:block_start + 900]
    assert "bundle_refs=" in block
    assert 'metadata.get("bundle_ref")' in block


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
