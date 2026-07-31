"""
Experiment 161: automatic disk-space guard for the persistent /data volume.

Two live incidents in one day (2026-08-01) hit
`sqlite3.OperationalError: database or disk is full` on plain DB writes
(even user registration) because generated_projects/ node_modules trees
accumulate on the persistent volume forever with no automatic cleanup --
confirmed by /admin/data-dir showing node_modules as the overwhelming
majority of the 467.8/500MB (93.6%) in use both times. Manual cleanup via
/admin/clean-node-modules recovered 200-250MB each time. This makes that
sweep automatic instead of admin-triggered-only.

Run directly: python tests/reliability/test_exp161_storage_guard.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.services.storage_cleanup import (
    clean_stale_node_modules,
    clean_stale_node_modules_if_needed,
    disk_usage_ratio,
    DISK_CLEANUP_THRESHOLD,
)


def _mk_project_with_node_modules(root: Path, name: str, n_files: int = 3) -> Path:
    proj = root / name
    nm = proj / "node_modules"
    nm.mkdir(parents=True)
    for i in range(n_files):
        (nm / f"pkg{i}.js").write_text("x" * 1000, encoding="utf-8")
    (proj / "app.zip").write_bytes(b"not a real zip, just a static artifact")
    return proj


def test_clean_stale_node_modules_removes_all_by_default():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _mk_project_with_node_modules(root, "todo_app")
        _mk_project_with_node_modules(root, "blog_app")

        result = clean_stale_node_modules(root=str(root))

        assert result["removed_count"] == 2
        assert result["bytes_freed"] > 0
        assert not (root / "todo_app" / "node_modules").exists()
        assert not (root / "blog_app" / "node_modules").exists()
        # Static zip artifacts are never touched.
        assert (root / "todo_app" / "app.zip").exists()
        assert (root / "blog_app" / "app.zip").exists()


def test_clean_stale_node_modules_excludes_in_flight_project():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _mk_project_with_node_modules(root, "old_app")
        _mk_project_with_node_modules(root, "current_app")

        result = clean_stale_node_modules(root=str(root), exclude_project="current_app")

        assert result["removed_count"] == 1
        assert not (root / "old_app" / "node_modules").exists()
        # The in-flight project's node_modules must survive untouched.
        assert (root / "current_app" / "node_modules").exists()
        assert (root / "current_app" / "node_modules" / "pkg0.js").exists()


def test_clean_stale_node_modules_noop_on_missing_root():
    result = clean_stale_node_modules(root="/definitely/does/not/exist")
    assert result["removed_count"] == 0
    assert result["bytes_freed"] == 0


def test_disk_usage_ratio_is_a_fraction_between_0_and_1():
    ratio = disk_usage_ratio(".")
    assert 0.0 <= ratio <= 1.0


def test_guard_skips_sweep_when_usage_below_threshold():
    with patch("app.services.storage_cleanup.disk_usage_ratio", return_value=DISK_CLEANUP_THRESHOLD - 0.1):
        with patch("app.services.storage_cleanup.clean_stale_node_modules") as mock_clean:
            result = clean_stale_node_modules_if_needed()
    assert result is None
    mock_clean.assert_not_called()


def test_guard_sweeps_when_usage_at_or_above_threshold():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _mk_project_with_node_modules(root, "todo_app")
        with patch("app.services.storage_cleanup.disk_usage_ratio", return_value=DISK_CLEANUP_THRESHOLD + 0.1):
            result = clean_stale_node_modules_if_needed(exclude_project=None)
            # clean_stale_node_modules_if_needed always targets the real
            # generated_projects root internally -- verify the guard at
            # least attempted a real sweep call by checking it returns the
            # expected result shape rather than None.
    assert result is not None
    assert "removed_count" in result
    assert "disk_free_bytes_after" in result


def test_guard_never_raises_on_internal_failure():
    with patch("app.services.storage_cleanup.disk_usage_ratio", side_effect=RuntimeError("boom")):
        result = clean_stale_node_modules_if_needed()  # must not raise
    assert result is None


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
