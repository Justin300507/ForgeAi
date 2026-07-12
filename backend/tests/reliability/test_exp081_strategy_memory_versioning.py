"""
Experiment 081 (Version Retry Strategy Memory): regression tests for the
generation-tag mechanism added to app/retry/strategy_memory.py.

Root cause (Exp080, confirmed via strategy_outcomes.json's own git
history): regenerate_module's 0-success blacklist for contract/api/
SyntaxError was recorded entirely before ef9eebc's syntax-validation gate
(2026-07-11) and Exp078's endpoint-preservation wiring (2026-07-12) --
i.e. under an implementation that no longer exists -- yet
strategy_outcomes.json stores only a lifetime tally with no timestamp or
version, so should_skip() could never know the underlying code changed.

Fix: a per-strategy "current generation" table (_STRATEGY_GENERATIONS,
regenerate_module bumped to 2 this experiment) plus a "generation" field
stamped on every stored entry. On load, any entry whose generation is
older than its strategy's current generation (a missing field counts as
generation 1, what every pre-existing entry implicitly is) gets reset to
zero -- and ONLY that entry; every other (pattern, strategy) pair is left
byte-for-byte untouched, since their strategy's generation was never
bumped.

Run directly: python tests/reliability/test_exp081_strategy_memory_versioning.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import app.retry.strategy_memory as sm


def _isolated_store(tmpdir: str) -> Path:
    """Point the module's storage path at a throwaway file for this test,
    and reset it to a clean, non-monkeypatched state afterward isn't
    needed -- each test gets its own tempdir, so no cross-test leakage."""
    path = Path(tmpdir) / "strategy_outcomes.json"
    sm._STORE_PATH = path
    return path


def _write_raw(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


# ---------------------------------------------------------------------------
# Migration from legacy entries (no "generation" field at all)
# ---------------------------------------------------------------------------

def test_migration_resets_only_the_bumped_strategy():
    with tempfile.TemporaryDirectory() as td:
        path = _isolated_store(td)
        _write_raw(path, {
            "contract": {
                "regenerate_module": {"successes": 0, "tries": 3},
                "patch_file": {"successes": 50, "tries": 126},
            },
        })
        data = sm._load()
        assert data["contract"]["regenerate_module"] == {
            "successes": 0, "tries": 0, "generation": 2,
        }, "regenerate_module's legacy entry must reset to zero, stamped gen 2"
        assert data["contract"]["patch_file"] == {"successes": 50, "tries": 126}, (
            "patch_file must be byte-for-byte untouched -- no generation field "
            "added, no counts changed, since patch_file was never bumped"
        )


def test_migration_reproduces_exact_production_snapshot():
    # The actual frozen strategy_outcomes.json content from today (Exp080's
    # own evidence), reconstructed here rather than touching the real file.
    with tempfile.TemporaryDirectory() as td:
        path = _isolated_store(td)
        _write_raw(path, {
            "AttributeError": {
                "patch_file": {"successes": 0, "tries": 2},
                "regenerate_module": {"successes": 0, "tries": 3},
                "switch_model": {"successes": 0, "tries": 1},
            },
            "ImportError": {
                "regenerate_module": {"successes": 1, "tries": 1},
                "switch_model": {"successes": 1, "tries": 1},
            },
            "SyntaxError": {
                "patch_file": {"successes": 2, "tries": 6},
                "regenerate_module": {"successes": 0, "tries": 2},
            },
            "api": {
                "patch_file": {"successes": 0, "tries": 3},
                "regenerate_arch": {"successes": 1, "tries": 1},
                "regenerate_module": {"successes": 0, "tries": 3},
                "switch_model": {"successes": 4, "tries": 8},
            },
            "contract": {
                "patch_file": {"successes": 50, "tries": 126},
                "regenerate_arch": {"successes": 9, "tries": 42},
                "regenerate_module": {"successes": 0, "tries": 3},
                "switch_model": {"successes": 0, "tries": 3},
            },
        })
        data = sm._load()

        # Every regenerate_module entry, across every pattern -- including
        # ImportError's 1/1 SUCCESS -- must reset, because the STRATEGY
        # changed, not because any individual pattern's outcome was "wrong".
        for pattern in ("AttributeError", "ImportError", "SyntaxError", "api", "contract"):
            assert data[pattern]["regenerate_module"] == {
                "successes": 0, "tries": 0, "generation": 2,
            }, f"{pattern}/regenerate_module must reset"

        # Every non-regenerate_module entry must be completely untouched.
        assert data["AttributeError"]["patch_file"] == {"successes": 0, "tries": 2}
        assert data["AttributeError"]["switch_model"] == {"successes": 0, "tries": 1}
        assert data["ImportError"]["switch_model"] == {"successes": 1, "tries": 1}
        assert data["SyntaxError"]["patch_file"] == {"successes": 2, "tries": 6}
        assert data["api"]["patch_file"] == {"successes": 0, "tries": 3}
        assert data["api"]["regenerate_arch"] == {"successes": 1, "tries": 1}
        assert data["api"]["switch_model"] == {"successes": 4, "tries": 8}
        assert data["contract"]["patch_file"] == {"successes": 50, "tries": 126}
        assert data["contract"]["regenerate_arch"] == {"successes": 9, "tries": 42}
        assert data["contract"]["switch_model"] == {"successes": 0, "tries": 3}


# ---------------------------------------------------------------------------
# Generation mismatch / match (explicit "generation" field present)
# ---------------------------------------------------------------------------

def test_explicit_older_generation_resets():
    with tempfile.TemporaryDirectory() as td:
        path = _isolated_store(td)
        _write_raw(path, {
            "contract": {"regenerate_module": {"successes": 0, "tries": 5, "generation": 1}},
        })
        data = sm._load()
        assert data["contract"]["regenerate_module"] == {
            "successes": 0, "tries": 0, "generation": 2,
        }


def test_matching_generation_is_preserved_exactly():
    with tempfile.TemporaryDirectory() as td:
        path = _isolated_store(td)
        entry = {"successes": 1, "tries": 4, "generation": 2}
        _write_raw(path, {"contract": {"regenerate_module": dict(entry)}})
        data = sm._load()
        assert data["contract"]["regenerate_module"] == entry, (
            "an entry already on the current generation must not be reset, "
            "even though it happens to have 0-success-like statistics"
        )


def test_future_generation_is_not_touched():
    # An entry recorded under a generation NEWER than what's currently
    # configured (e.g. code was rolled back) should never be "reset for
    # being ahead" -- only entry_gen < current_gen triggers a reset.
    with tempfile.TemporaryDirectory() as td:
        path = _isolated_store(td)
        entry = {"successes": 2, "tries": 5, "generation": 5}
        _write_raw(path, {"contract": {"regenerate_module": dict(entry)}})
        data = sm._load()
        assert data["contract"]["regenerate_module"] == entry


# ---------------------------------------------------------------------------
# Persistence across reloads -- resets exactly once
# ---------------------------------------------------------------------------

def test_reset_persists_and_does_not_repeat():
    with tempfile.TemporaryDirectory() as td:
        path = _isolated_store(td)
        _write_raw(path, {
            "contract": {"regenerate_module": {"successes": 0, "tries": 3}},
        })

        first = sm._load()
        assert first["contract"]["regenerate_module"]["generation"] == 2

        # The file on disk must already reflect the reset -- not just the
        # in-memory return value -- so a fresh process reading the file
        # independently sees the corrected state too.
        on_disk = json.loads(path.read_text(encoding="utf-8"))
        assert on_disk["contract"]["regenerate_module"] == {
            "successes": 0, "tries": 0, "generation": 2,
        }

        save_calls = {"count": 0}
        real_save = sm._save

        def counting_save(data):
            save_calls["count"] += 1
            real_save(data)

        sm._save = counting_save
        try:
            second = sm._load()
            third = sm._load()
        finally:
            sm._save = real_save

        assert second == first
        assert third == first
        assert save_calls["count"] == 0, (
            "no migration should re-fire (and no _save() should happen) once "
            "an entry is already stamped with the current generation"
        )


# ---------------------------------------------------------------------------
# record_outcome() stamps generation on every write
# ---------------------------------------------------------------------------

def test_record_outcome_stamps_generation_on_new_entry():
    with tempfile.TemporaryDirectory() as td:
        _isolated_store(td)
        sm.record_outcome("contract", "regenerate_module", improved=True)
        data = sm._load()
        assert data["contract"]["regenerate_module"] == {
            "successes": 1, "tries": 1, "generation": 2,
        }


def test_record_outcome_stamps_generation_on_existing_untagged_entry():
    with tempfile.TemporaryDirectory() as td:
        path = _isolated_store(td)
        _write_raw(path, {"contract": {"patch_file": {"successes": 50, "tries": 126}}})
        sm.record_outcome("contract", "patch_file", improved=False)
        data = sm._load()
        assert data["contract"]["patch_file"] == {
            "successes": 50, "tries": 127, "generation": 1,
        }


# ---------------------------------------------------------------------------
# End-to-end: should_skip() reflects the reset immediately
# ---------------------------------------------------------------------------

def test_should_skip_false_immediately_after_reset():
    with tempfile.TemporaryDirectory() as td:
        path = _isolated_store(td)
        _write_raw(path, {
            "contract": {"regenerate_module": {"successes": 0, "tries": 3}},
        })
        # Before the fix, this would have been True (3 tries, 0 successes) --
        # the exact Exp079/080-confirmed permanent-skip condition.
        assert sm.should_skip("contract", "regenerate_module") is False


def test_should_skip_still_true_for_untouched_strategy():
    with tempfile.TemporaryDirectory() as td:
        path = _isolated_store(td)
        _write_raw(path, {
            "contract": {"switch_model": {"successes": 0, "tries": 3}},
        })
        assert sm.should_skip("contract", "switch_model") is True, (
            "switch_model was never bumped -- its genuinely-still-valid "
            "0/3 blacklist must be preserved exactly as before"
        )


def test_subsequent_runs_accumulate_fresh_evidence_normally():
    with tempfile.TemporaryDirectory() as td:
        path = _isolated_store(td)
        _write_raw(path, {
            "contract": {"regenerate_module": {"successes": 0, "tries": 3}},
        })
        sm._load()  # triggers the one-time reset
        sm.record_outcome("contract", "regenerate_module", improved=True)
        sm.record_outcome("contract", "regenerate_module", improved=False)
        data = sm._load()
        assert data["contract"]["regenerate_module"] == {
            "successes": 1, "tries": 2, "generation": 2,
        }
        assert sm.should_skip("contract", "regenerate_module") is False, (
            "1 success among 2 tries must never be skip-eligible (should_skip "
            "requires successes == 0)"
        )


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
