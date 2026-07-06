"""
Verifies JourneyResult carries structured seed telemetry captured from
the journey runner's existing best-effort POST /seed call, and that
backend_runner.py surfaces it in journey_data. Plain assert-based --
run directly: python tests/adr002/test_journey_seed_telemetry.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.runtime.user_journey_runner import JourneyResult


def test_journey_result_has_seed_summary_field_defaulting_empty():
    result = JourneyResult(success=True)
    assert result.seed_summary == {}


def test_journey_result_accepts_seed_summary():
    result = JourneyResult(success=True, seed_summary={"priorities": {"inserted": 3}})
    assert result.seed_summary == {"priorities": {"inserted": 3}}


def test_backend_runner_surfaces_seed_summary_in_journey_data():
    src_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "app", "runtime", "backend_runner.py"
    )
    with open(src_path, "r", encoding="utf-8") as f:
        src = f.read()
    block_start = src.index('journey_data = {')
    block = src[block_start:block_start + 800]
    assert "seed_summary" in block


def test_journey_runner_captures_seed_response_json():
    src_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "app", "runtime", "user_journey_runner.py"
    )
    with open(src_path, "r", encoding="utf-8") as f:
        src = f.read()
    seed_block_start = src.index("Reference-data seed")
    seed_block = src[seed_block_start:seed_block_start + 1400]
    assert "seed_summary" in seed_block
    assert ".json()" in seed_block


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
