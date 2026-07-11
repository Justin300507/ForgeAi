"""
Canary lock: refuse a second concurrent `run_canary.py`. Added after an
operator accidentally ran two canaries at once on 2026-07-11 (a shell-cwd
mistake), wasting a full duplicate 3-app generation before it was caught by
hand. This makes that class of mistake impossible instead of relying on
catching it.

Run directly: python tests/reliability/test_canary_lock.py
"""
import json
import os
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))

import run_canary


def _with_lock_path(tmp_path):
    return mock.patch.object(run_canary, "LOCK_PATH", tmp_path / ".canary.lock")


def test_acquire_creates_lock_with_current_pid(tmp_path):
    with _with_lock_path(tmp_path):
        run_canary._acquire_lock("test-label")
        assert run_canary.LOCK_PATH.exists()
        info = json.loads(run_canary.LOCK_PATH.read_text(encoding="utf-8"))
        assert info["pid"] == os.getpid()
        assert info["label"] == "test-label"


def test_acquire_refuses_when_lock_held_by_live_process(tmp_path):
    with _with_lock_path(tmp_path):
        # Use our own PID to simulate a definitely-alive holder.
        run_canary.LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        run_canary.LOCK_PATH.write_text(json.dumps({
            "pid": os.getpid(), "started": "2026-07-11T00:00:00Z", "label": "prior-run",
        }), encoding="utf-8")
        try:
            run_canary._acquire_lock("new-run")
            raised = False
        except SystemExit as e:
            raised = True
            assert e.code == 2
        assert raised, "acquire must refuse (SystemExit 2) when the lock's PID is alive"


def test_acquire_reclaims_stale_lock():
    tmp_path = Path(run_canary._BACKEND_ROOT) / "benchmark_results" / "_test_stale_lock_dir"
    tmp_path.mkdir(parents=True, exist_ok=True)
    try:
        with _with_lock_path(tmp_path):
            # A PID essentially guaranteed not to exist.
            run_canary.LOCK_PATH.write_text(json.dumps({
                "pid": 2**30, "started": "2020-01-01T00:00:00Z", "label": "dead-run",
            }), encoding="utf-8")
            run_canary._acquire_lock("new-run")  # must NOT raise
            info = json.loads(run_canary.LOCK_PATH.read_text(encoding="utf-8"))
            assert info["pid"] == os.getpid()
    finally:
        import shutil
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_release_removes_lock_and_is_idempotent(tmp_path):
    with _with_lock_path(tmp_path):
        run_canary._acquire_lock(None)
        assert run_canary.LOCK_PATH.exists()
        run_canary._release_lock()
        assert not run_canary.LOCK_PATH.exists()
        run_canary._release_lock()  # no lock file -- must not raise


if __name__ == "__main__":
    import tempfile
    import traceback

    tests = [obj for name, obj in list(globals().items()) if name.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            with tempfile.TemporaryDirectory() as td:
                if "tmp_path" in t.__code__.co_varnames[:t.__code__.co_argcount]:
                    t(Path(td))
                else:
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
