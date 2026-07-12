"""
Experiment 067: regression tests for hardening the write call inside
app/repair/orchestrator.py::_regenerate_module() -- the third,
independent write path Exp066 found and deliberately left untouched
(repair logic was out of scope for that experiment).

This experiment's own audit (Part 1) found the premise "_regenerate_module
is the only remaining unsafe write path" was not quite accurate: the
same _safe_patch_target() + target.write_text() pattern is used by 4
OTHER call sites inside orchestrator.py's _apply_fix_group() (lines
~404, 615, 635, 764). Only _regenerate_module()'s own write call
(previously line 831) was named in scope by this experiment's mission,
so only it was changed -- see docs/WRITE_VALIDATION_MATRIX.md for the
full accounting of what stayed untouched and why.

What changed inside _regenerate_module() itself:
  - a new has_windows_drive_or_unc() check, scoped to this function's
    own loop (NOT inside _safe_patch_target(), which stays shared and
    unmodified across all 5 call sites)
  - target.write_text() replaced with atomic_write_text()

_safe_patch_target() itself is NOT modified by this experiment -- these
tests characterize its ALREADY-EXISTING, empirically-verified behavior
(not assumed) alongside the new additions, so a future regression in
either the untouched function or the new check is caught.

The real generation call (generate_architecture_fix, an LLM call) is
monkeypatched per this experiment's "no API usage" rule -- everything
downstream of that call (path validation, syntax gate, atomic write)
runs for real, unmocked.

Run directly: python tests/reliability/test_exp067_regenerate_module_hardening.py
"""
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.repair.orchestrator import _regenerate_module, _safe_patch_target
from app.utils import atomic_write as atomic_write_module
from app.core.context import GenerationContext, DiagnosticGroup, FixStrategy
from app.retry.manager import StrategyConfig


def _ctx(project_path):
    return GenerationContext(job_id="t", idea="t", project_path=project_path, project_name="t")


def _group(affected_files):
    return DiagnosticGroup(
        group_id="g1", root_cause="test", diagnostics=[],
        affected_files=affected_files,
        suggested_strategy=FixStrategy.REGENERATE_MODULE, priority=1,
    )


def _cfg():
    return StrategyConfig(
        attempt=1, strategy=FixStrategy.REGENERATE_MODULE,
        provider="none", model_hint="none",
    )


def _run_regenerate_module(project_path, files_returned, affected=("app/routes/x.py",)):
    def fake_generate_architecture_fix(architecture, messages, provider, required_endpoints=None):
        return {"files": files_returned}

    with mock.patch(
        "app.services.architecture_fix_service.generate_architecture_fix",
        side_effect=fake_generate_architecture_fix,
    ):
        return _regenerate_module(_group(list(affected)), _ctx(project_path), _cfg())


# ---------------------------------------------------------------------------
# _safe_patch_target() -- characterizing its existing (unmodified) behavior
# ---------------------------------------------------------------------------

def test_safe_patch_target_accepts_relative_path():
    with tempfile.TemporaryDirectory() as base:
        project = Path(base) / "project"
        project.mkdir()
        target = _safe_patch_target(project, "app/routes/x.py")
        assert target is not None
        assert str(target).startswith(str(project.resolve()))


def test_safe_patch_target_rejects_single_level_traversal():
    with tempfile.TemporaryDirectory() as base:
        project = Path(base) / "project"
        project.mkdir()
        assert _safe_patch_target(project, "../escape.py") is None


def test_safe_patch_target_rejects_multi_segment_traversal():
    with tempfile.TemporaryDirectory() as base:
        project = Path(base) / "project"
        project.mkdir()
        assert _safe_patch_target(project, "../../escape.py") is None


def test_safe_patch_target_rejects_posix_absolute_path_outside_root():
    with tempfile.TemporaryDirectory() as base:
        project = Path(base) / "project"
        project.mkdir()
        assert _safe_patch_target(project, "/etc/passwd") is None


def test_safe_patch_target_accepts_absolute_path_inside_root():
    # Documented, intentional allowance (see the function's own
    # docstring): fix prompts echo back absolute diagnostic paths, and
    # the LLM often repeats them verbatim. This is the exact reason
    # this function was NOT replaced by resolve_safe_path() (which
    # rejects ALL absolute paths unconditionally) -- verified here so a
    # future change doesn't silently break this real, load-bearing
    # allowance.
    with tempfile.TemporaryDirectory() as base:
        project = Path(base) / "project"
        project.mkdir()
        abs_inside = str(project / "app" / "routes" / "x.py")
        target = _safe_patch_target(project, abs_inside)
        assert target is not None


def test_safe_patch_target_rejects_windows_drive_absolute_path():
    with tempfile.TemporaryDirectory() as base:
        project = Path(base) / "project"
        project.mkdir()
        assert _safe_patch_target(project, "C:\\Windows\\evil.dll") is None


def test_safe_patch_target_rejects_unc_path():
    with tempfile.TemporaryDirectory() as base:
        project = Path(base) / "project"
        project.mkdir()
        assert _safe_patch_target(project, r"\\server\share\evil.py") is None


def test_safe_patch_target_symlink_escape_rejected_if_supported():
    with tempfile.TemporaryDirectory() as base:
        project = Path(base) / "project"
        outside = Path(base) / "outside"
        project.mkdir()
        outside.mkdir()
        (outside / "secret.py").write_text("secret = 1\n")

        link_path = project / "link_to_outside"
        try:
            os.symlink(outside, link_path, target_is_directory=True)
        except (OSError, NotImplementedError) as e:
            print(f"  SKIPPED (no symlink privilege in this environment: {e})")
            return

        try:
            target = _safe_patch_target(project, "link_to_outside/secret.py")
            assert target is None, (
                "a symlink planted inside the project pointing outside it "
                "must still be rejected"
            )
        finally:
            os.remove(link_path)


# ---------------------------------------------------------------------------
# _regenerate_module() -- the new drive/UNC check + atomic write, exercised
# through the real function with only the LLM call monkeypatched
# ---------------------------------------------------------------------------

def test_regenerate_module_normal_regeneration_writes_files():
    with tempfile.TemporaryDirectory() as td:
        project_path = Path(td)
        modified, fix_content_map = _run_regenerate_module(project_path, [
            {"path": "app/routes/x.py", "content": "x = 1\n"},
        ])
        assert modified == ["app/routes/x.py"]
        assert (project_path / "app" / "routes" / "x.py").read_text() == "x = 1\n"


def test_regenerate_module_writes_nested_files():
    with tempfile.TemporaryDirectory() as td:
        project_path = Path(td)
        modified, _ = _run_regenerate_module(project_path, [
            {"path": "app/routes/nested/deep/y.py", "content": "y = 2\n"},
        ])
        assert modified == ["app/routes/nested/deep/y.py"]
        assert (project_path / "app" / "routes" / "nested" / "deep" / "y.py").is_file()


def test_regenerate_module_rejects_relative_traversal():
    with tempfile.TemporaryDirectory() as td:
        project_path = Path(td)
        modified, _ = _run_regenerate_module(project_path, [
            {"path": "../escape.py", "content": "evil = 1\n"},
            {"path": "app/routes/ok.py", "content": "ok = 1\n"},
        ])
        assert modified == ["app/routes/ok.py"]
        escape = (project_path / ".." / "escape.py").resolve()
        assert not escape.exists()


def test_regenerate_module_rejects_multi_segment_traversal():
    with tempfile.TemporaryDirectory() as td:
        project_path = Path(td)
        modified, _ = _run_regenerate_module(project_path, [
            {"path": "../../escape.py", "content": "evil = 1\n"},
        ])
        assert modified == []


def test_regenerate_module_new_check_rejects_windows_drive_absolute():
    with tempfile.TemporaryDirectory() as td:
        project_path = Path(td)
        modified, _ = _run_regenerate_module(project_path, [
            {"path": "C:\\Windows\\evil.dll", "content": "evil\n"},
        ])
        assert modified == []


def test_regenerate_module_new_check_rejects_windows_drive_relative():
    # This is the one shape _safe_patch_target() alone does NOT catch
    # (see test file's module docstring) -- confirms the new
    # has_windows_drive_or_unc() check inside _regenerate_module closes
    # it for this write path specifically.
    with tempfile.TemporaryDirectory() as td:
        project_path = Path(td)
        modified, _ = _run_regenerate_module(project_path, [
            {"path": "C:evil.py", "content": "evil\n"},
        ])
        assert modified == [], (
            "drive-relative Windows path should now be rejected by the "
            "new Exp067 check before it ever reaches _safe_patch_target"
        )
        assert not (project_path / "evil.py").exists(), (
            "before Exp067 this shape silently landed inside the project "
            "root under a prefix-stripped name -- must no longer happen"
        )


def test_regenerate_module_rejects_unc_path():
    with tempfile.TemporaryDirectory() as td:
        project_path = Path(td)
        modified, _ = _run_regenerate_module(project_path, [
            {"path": r"\\server\share\evil.py", "content": "evil\n"},
        ])
        assert modified == []


def test_regenerate_module_rejects_posix_absolute_path_outside_root():
    with tempfile.TemporaryDirectory() as td:
        project_path = Path(td)
        modified, _ = _run_regenerate_module(project_path, [
            {"path": "/etc/passwd", "content": "evil\n"},
        ])
        assert modified == []


def test_regenerate_module_rejects_syntax_error_python():
    with tempfile.TemporaryDirectory() as td:
        project_path = Path(td)
        modified, _ = _run_regenerate_module(project_path, [
            {"path": "app/routes/broken.py", "content": "x = (\n"},
            {"path": "app/routes/ok.py", "content": "ok = 1\n"},
        ])
        assert modified == ["app/routes/ok.py"]
        assert not (project_path / "app" / "routes" / "broken.py").exists()


def test_regenerate_module_duplicate_path_last_write_wins():
    with tempfile.TemporaryDirectory() as td:
        project_path = Path(td)
        modified, _ = _run_regenerate_module(project_path, [
            {"path": "app/utils/dup.py", "content": "value = 1\n"},
            {"path": "app/utils/dup.py", "content": "value = 2\n"},
        ])
        assert modified == ["app/utils/dup.py", "app/utils/dup.py"], (
            "modified-list append behavior is pre-existing and unchanged "
            "by this experiment -- both entries recorded, same as before"
        )
        assert (project_path / "app" / "utils" / "dup.py").read_text() == "value = 2\n"


def test_regenerate_module_mixed_valid_and_malicious_batch():
    with tempfile.TemporaryDirectory() as td:
        project_path = Path(td)
        modified, _ = _run_regenerate_module(project_path, [
            {"path": "app/routes/x.py", "content": "x = 1\n"},
            {"path": "../escape.py", "content": "evil\n"},
            {"path": "app/routes/y.py", "content": "y = 2\n"},
            {"path": "C:evil.py", "content": "evil\n"},
        ])
        assert set(modified) == {"app/routes/x.py", "app/routes/y.py"}


def test_regenerate_module_write_failure_does_not_corrupt_existing_file():
    # _regenerate_module's pre-existing except-fallback (unchanged by
    # this experiment) catches ANY exception from the try block --
    # including one raised by our simulated atomic-write failure -- and
    # falls back to _apply_fix_group(), which is NOT mocked here and can
    # itself reach a real LLM call on a cache miss. That fallback path
    # is irrelevant to what this test verifies (atomic-write rollback);
    # it's stubbed out to a no-op so the test stays offline, per this
    # experiment's "no API usage" rule. Confirmed necessary empirically:
    # without this stub, this test made a real Cerebras call.
    with tempfile.TemporaryDirectory() as td:
        project_path = Path(td)
        _run_regenerate_module(project_path, [
            {"path": "app/routes/x.py", "content": "original = 1\n"},
        ])

        real_replace = atomic_write_module.os.replace

        def failing_replace(*a, **k):
            raise OSError("simulated failure")

        atomic_write_module.os.replace = failing_replace
        try:
            with mock.patch(
                "app.repair.orchestrator._apply_fix_group",
                return_value=([], {}),
            ):
                _run_regenerate_module(project_path, [
                    {"path": "app/routes/x.py", "content": "corrupted = True\n"},
                ])
        finally:
            atomic_write_module.os.replace = real_replace

        content = (project_path / "app" / "routes" / "x.py").read_text()
        assert content == "original = 1\n", (
            f"a failed atomic write must not leave a corrupted file: {content!r}"
        )


def test_regenerate_module_falls_back_on_generation_exception():
    # Pre-existing behavior (the except-fallback to _apply_fix_group),
    # unchanged by this experiment -- verified it's still reachable
    # after adding the new drive/UNC check inline in the try block.
    # _apply_fix_group itself is stubbed (see the test above for why:
    # unmocked, it can make a real LLM call on a cache miss, which this
    # experiment's rules forbid) -- this test only verifies that
    # _regenerate_module ROUTES to the fallback and does not propagate
    # the exception itself, not what the fallback does internally
    # (that's _apply_fix_group's own, separately-tested behavior).
    with tempfile.TemporaryDirectory() as td:
        project_path = Path(td)

        def raising_fix(architecture, messages, provider, required_endpoints=None):
            raise RuntimeError("simulated generation failure")

        with mock.patch(
            "app.services.architecture_fix_service.generate_architecture_fix",
            side_effect=raising_fix,
        ), mock.patch(
            "app.repair.orchestrator._apply_fix_group",
            return_value=(["fallback_used.py"], {"fallback_used.py": "x"}),
        ) as fallback:
            modified, fix_content_map = _regenerate_module(
                _group(["app/routes/x.py"]), _ctx(project_path), _cfg()
            )
        assert fallback.called
        assert modified == ["fallback_used.py"]


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
