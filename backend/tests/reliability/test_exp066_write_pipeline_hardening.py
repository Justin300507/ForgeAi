"""
Experiment 066: regression tests for the write-pipeline hardening pass.

Covers the two new shared modules (app/utils/safe_path.py,
app/utils/atomic_write.py) and their integration into both write
entrypoints (app/services/file_writer_service.py::write_files -- the
bulk initial-generation writer that had ZERO path validation before
this experiment, per docs/SECURITY_REVIEW.md Finding #1 -- and
app/services/fix_writer_service.py::write_fix -- the single-file
repair-time writer, whose narrower pre-existing inline traversal check
this experiment replaced with the shared validator).

Run directly: python tests/reliability/test_exp066_write_pipeline_hardening.py
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.utils.safe_path import resolve_safe_path, is_safe_path, PathTraversalError
from app.utils import atomic_write as atomic_write_module
from app.utils.atomic_write import atomic_write_text
from app.services.fix_writer_service import write_fix
from app.services.file_writer_service import write_files


# ---------------------------------------------------------------------------
# app/utils/safe_path.py -- unit tests
# ---------------------------------------------------------------------------

def test_safe_path_accepts_simple_relative_path():
    with tempfile.TemporaryDirectory() as base:
        resolved = resolve_safe_path(base, "app/routes/x.py")
        assert str(resolved).startswith(os.path.abspath(base))


def test_safe_path_accepts_deeply_nested_relative_path():
    with tempfile.TemporaryDirectory() as base:
        resolved = resolve_safe_path(base, "src/pages/nested/deep/Foo.jsx")
        assert str(resolved).startswith(os.path.abspath(base))


def test_safe_path_accepts_posix_style_separators():
    with tempfile.TemporaryDirectory() as base:
        resolved = resolve_safe_path(base, "a/b/c/d.py")
        assert str(resolved).startswith(os.path.abspath(base))


def test_safe_path_accepts_windows_style_separators():
    with tempfile.TemporaryDirectory() as base:
        # backslash-separated relative path (as an LLM targeting Windows
        # conventions might emit) must still resolve inside base.
        resolved = resolve_safe_path(base, "a\\b\\c.py")
        assert str(resolved).startswith(os.path.abspath(base))


def test_safe_path_accepts_dot_current_dir():
    with tempfile.TemporaryDirectory() as base:
        resolved = resolve_safe_path(base, ".")
        assert resolved == __import__("pathlib").Path(base).resolve()


def test_safe_path_rejects_single_level_traversal():
    with tempfile.TemporaryDirectory() as base:
        project = os.path.join(base, "project")
        os.makedirs(project)
        try:
            resolve_safe_path(project, "../escape.py")
            assert False, "should have raised"
        except PathTraversalError:
            pass


def test_safe_path_rejects_multi_segment_traversal():
    with tempfile.TemporaryDirectory() as base:
        project = os.path.join(base, "project")
        os.makedirs(project)
        try:
            resolve_safe_path(project, "../../escape.py")
            assert False, "should have raised"
        except PathTraversalError:
            pass


def test_safe_path_rejects_traversal_disguised_inside_valid_prefix():
    with tempfile.TemporaryDirectory() as base:
        project = os.path.join(base, "project")
        os.makedirs(project)
        # "a/../../b" -- a naive string startswith("..") check on the raw
        # string would MISS this (the string doesn't start with ".."),
        # but the resolved path still escapes. This is exactly the class
        # of bug the resolved-path-containment approach fixes over
        # write_fix's old norm.startswith("..") check.
        try:
            resolve_safe_path(project, "a/../../escape.py")
            assert False, "should have raised"
        except PathTraversalError:
            pass


def test_safe_path_rejects_posix_absolute_path():
    with tempfile.TemporaryDirectory() as base:
        assert not is_safe_path(base, "/etc/passwd")


def test_safe_path_rejects_windows_drive_path():
    with tempfile.TemporaryDirectory() as base:
        assert not is_safe_path(base, "C:\\Windows\\System32\\evil.dll")


def test_safe_path_rejects_windows_drive_relative_path():
    with tempfile.TemporaryDirectory() as base:
        # "C:evil.py" (drive-relative, no backslash) is a distinct, more
        # obscure Windows path shape from "C:\evil.py" -- PureWindowsPath
        # still populates .drive for it, so it must be rejected too.
        assert not is_safe_path(base, "C:evil.py")


def test_safe_path_rejects_unc_path():
    with tempfile.TemporaryDirectory() as base:
        assert not is_safe_path(base, r"\\server\share\evil.py")


def test_safe_path_rejects_empty_path():
    with tempfile.TemporaryDirectory() as base:
        assert not is_safe_path(base, "")
        assert not is_safe_path(base, "   ")


def test_safe_path_symlink_escape_rejected_if_supported():
    # Symlink creation requires admin/dev-mode privileges on Windows;
    # this environment does not have them (confirmed: os.symlink raises
    # WinError 1314). Skip gracefully rather than fail on an environment
    # limitation -- the protection itself relies on Python's own
    # documented Path.resolve() symlink-following behavior, not
    # anything this module invents.
    with tempfile.TemporaryDirectory() as base:
        project = os.path.join(base, "project")
        outside = os.path.join(base, "outside")
        os.makedirs(project)
        os.makedirs(outside)
        target = os.path.join(outside, "secret.py")
        with open(target, "w") as f:
            f.write("secret = 1\n")

        link_path = os.path.join(project, "link_to_outside")
        try:
            os.symlink(outside, link_path, target_is_directory=True)
        except (OSError, NotImplementedError) as e:
            print(f"  SKIPPED (no symlink privilege in this environment: {e})")
            return

        try:
            assert not is_safe_path(project, "link_to_outside/secret.py")
        finally:
            os.remove(link_path)


# ---------------------------------------------------------------------------
# app/utils/atomic_write.py -- unit tests
# ---------------------------------------------------------------------------

def test_atomic_write_creates_file_with_exact_content():
    with tempfile.TemporaryDirectory() as base:
        target = os.path.join(base, "a", "b", "file.py")
        atomic_write_text(target, "hello = 1\n")
        with open(target, encoding="utf-8") as f:
            assert f.read() == "hello = 1\n"


def test_atomic_write_creates_parent_directories():
    with tempfile.TemporaryDirectory() as base:
        target = os.path.join(base, "deep", "nested", "dir", "file.py")
        atomic_write_text(target, "x = 1\n")
        assert os.path.isfile(target)


def test_atomic_write_overwrites_existing_file():
    with tempfile.TemporaryDirectory() as base:
        target = os.path.join(base, "file.py")
        atomic_write_text(target, "old = 1\n")
        atomic_write_text(target, "new = 2\n")
        with open(target, encoding="utf-8") as f:
            assert f.read() == "new = 2\n"


def test_atomic_write_leaves_no_temp_files_behind_on_success():
    with tempfile.TemporaryDirectory() as base:
        target = os.path.join(base, "file.py")
        atomic_write_text(target, "x = 1\n")
        entries = os.listdir(base)
        assert entries == ["file.py"], f"unexpected leftover files: {entries}"


def test_atomic_write_rolls_back_on_replace_failure():
    # Simulate a crash/failure during the final os.replace() step and
    # verify: (1) the original file is untouched, (2) the temp file is
    # cleaned up rather than left behind.
    with tempfile.TemporaryDirectory() as base:
        target = os.path.join(base, "file.py")
        atomic_write_text(target, "original = 1\n")

        real_replace = atomic_write_module.os.replace

        def failing_replace(*a, **k):
            raise OSError("simulated failure during replace")

        atomic_write_module.os.replace = failing_replace
        try:
            try:
                atomic_write_text(target, "new_content_that_should_not_land\n")
                assert False, "should have raised"
            except OSError:
                pass
        finally:
            atomic_write_module.os.replace = real_replace

        with open(target, encoding="utf-8") as f:
            content = f.read()
        assert content == "original = 1\n", (
            f"original file was corrupted by a failed write: {content!r}"
        )

        leftover = [f for f in os.listdir(base) if f != "file.py"]
        assert not leftover, f"temp file(s) not cleaned up: {leftover}"


def test_atomic_write_rolls_back_on_write_failure():
    # Failure while writing the temp file's content (not just at
    # replace-time) must also leave the original file untouched and not
    # leak a temp file. Simulated by swapping os.fdopen for a wrapper
    # whose write() raises, so the real temp file still gets created
    # (exercising the actual cleanup path in atomic_write_text's except
    # block) rather than failing before any file exists.
    with tempfile.TemporaryDirectory() as base:
        target = os.path.join(base, "file.py")
        atomic_write_text(target, "original = 1\n")

        real_fdopen = atomic_write_module.os.fdopen

        class ExplodingFile:
            def __init__(self, real_f):
                self._f = real_f

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                self._f.close()
                return False

            def write(self, data):
                raise RuntimeError("simulated mid-write failure")

            def fileno(self):
                return self._f.fileno()

        def fake_fdopen(fd, *a, **k):
            return ExplodingFile(real_fdopen(fd, *a, **k))

        atomic_write_module.os.fdopen = fake_fdopen
        try:
            try:
                atomic_write_text(target, "corrupted_content\n")
                assert False, "should have raised"
            except RuntimeError:
                pass
        finally:
            atomic_write_module.os.fdopen = real_fdopen

        with open(target, encoding="utf-8") as f:
            content = f.read()
        assert content == "original = 1\n"

        leftover = [f for f in os.listdir(base) if f != "file.py"]
        assert not leftover, f"temp file(s) not cleaned up: {leftover}"


# ---------------------------------------------------------------------------
# write_files() integration -- path validation, duplicates, and
# interaction with the pre-existing syntax/semantic guards
# ---------------------------------------------------------------------------

def _fresh_project_dir(name):
    base = os.path.abspath(os.path.join(
        os.path.dirname(__file__), "..", "..", "..",
        "generated_projects", name,
    ))
    if os.path.exists(base):
        shutil.rmtree(base)
    return base


def test_write_files_rejects_traversal_without_crashing_the_batch():
    project = "exp066_test_write_files_traversal"
    base = _fresh_project_dir(project)
    try:
        files = [
            {"path": "../escape.py", "content": "evil = True\n"},
            {"path": "app/utils/valid.py", "content": "y = 2\n"},
        ]
        result = write_files(project, files, frontend_target="web", idea="")
        # Exp070: write_files() now resolves base_dir via resolve_safe_path()
        # (project_name traversal hardening), whose Path.resolve() call can
        # normalize Windows path casing (e.g. "onedrive" -> "OneDrive")
        # differently than this test's own os.path.join-based `base` --
        # a casing difference only, not a real path mismatch, so compared
        # case-insensitively rather than via strict string equality.
        assert os.path.normcase(result) == os.path.normcase(base)
        assert not os.path.exists(os.path.join(base, "..", "escape.py").replace("\\..\\", "\\"))
        assert not os.path.exists(os.path.abspath(os.path.join(base, "..", "escape.py")))
        assert os.path.exists(os.path.join(base, "app", "utils", "valid.py"))
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_write_files_duplicate_path_last_write_wins():
    project = "exp066_test_write_files_duplicates"
    base = _fresh_project_dir(project)
    try:
        files = [
            {"path": "app/utils/dup.py", "content": "value = 1\n"},
            {"path": "app/utils/dup.py", "content": "value = 2\n"},
        ]
        write_files(project, files, frontend_target="web", idea="")
        with open(os.path.join(base, "app", "utils", "dup.py"), encoding="utf-8") as f:
            content = f.read()
        assert content == "value = 2\n", (
            "duplicate-path writes should behave exactly as before this "
            "experiment (last entry in the list wins) -- atomic writes "
            "must not change this ordering behavior"
        )
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_write_files_skips_syntactically_invalid_python_without_crashing():
    project = "exp066_test_write_files_syntax_gap"
    base = _fresh_project_dir(project)
    try:
        files = [
            {"path": "app/routes/broken.py", "content": "x = (\n"},
            {"path": "app/routes/ok.py", "content": "y = 1\n"},
        ]
        write_files(project, files, frontend_target="web", idea="")
        assert not os.path.exists(os.path.join(base, "app", "routes", "broken.py"))
        assert os.path.exists(os.path.join(base, "app", "routes", "ok.py"))
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_write_files_skips_semantically_inconsistent_route_file():
    project = "exp066_test_write_files_semantic_gap"
    base = _fresh_project_dir(project)
    try:
        bad_route = (
            "from fastapi import APIRouter\n"
            "from pydantic import BaseModel\n\n"
            "router = APIRouter()\n\n\n"
            "class SignupRequest(BaseModel):\n"
            "    email: str\n"
            "    password: str\n\n\n"
            "@router.post('/auth/signup')\n"
            "def signup(req: SignupRequest):\n"
            "    return {'name': req.username}\n"
        )
        files = [
            {"path": "app/routes/auth_routes.py", "content": bad_route},
            {"path": "app/routes/ok.py", "content": "y = 1\n"},
        ]
        write_files(project, files, frontend_target="web", idea="")
        assert not os.path.exists(os.path.join(base, "app", "routes", "auth_routes.py")), (
            "write_files must apply the same Exp064 semantic guard "
            "write_fix already had -- this was the confirmed asymmetry "
            "in docs/VALIDATION_SYMMETRY (Exp065/066)"
        )
        assert os.path.exists(os.path.join(base, "app", "routes", "ok.py"))
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_write_files_accepts_windows_and_posix_style_paths_together():
    project = "exp066_test_write_files_mixed_separators"
    base = _fresh_project_dir(project)
    try:
        files = [
            {"path": "app\\routes\\windows_style.py", "content": "a = 1\n"},
            {"path": "app/routes/posix_style.py", "content": "b = 2\n"},
        ]
        write_files(project, files, frontend_target="web", idea="")
        assert os.path.exists(os.path.join(base, "app", "routes", "windows_style.py"))
        assert os.path.exists(os.path.join(base, "app", "routes", "posix_style.py"))
    finally:
        shutil.rmtree(base, ignore_errors=True)


# ---------------------------------------------------------------------------
# write_fix() integration -- regression-checks the pre-existing
# accept/reject behavior plus the new protections it gained
# ---------------------------------------------------------------------------

def test_write_fix_still_accepts_legit_relative_path():
    with tempfile.TemporaryDirectory() as project:
        assert write_fix(project, {"path": "app/routes/x.py", "content": "x = 1\n"}) is True
        assert os.path.isfile(os.path.join(project, "app", "routes", "x.py"))


def test_write_fix_still_rejects_single_level_traversal():
    with tempfile.TemporaryDirectory() as project:
        assert write_fix(project, {"path": "../escape.py", "content": "evil = 1\n"}) is False
        assert not os.path.exists(os.path.abspath(os.path.join(project, "..", "escape.py")))


def test_write_fix_now_also_rejects_windows_drive_path():
    # The OLD inline check (norm.startswith("..") or os.path.isabs(norm))
    # did not defend against this on a POSIX host, since os.path.isabs
    # is platform-dependent; the shared validator checks it explicitly
    # via PureWindowsPath regardless of host OS.
    with tempfile.TemporaryDirectory() as project:
        assert write_fix(project, {"path": "C:\\evil.py", "content": "evil = 1\n"}) is False


def test_write_fix_now_also_rejects_unc_path():
    with tempfile.TemporaryDirectory() as project:
        assert write_fix(project, {"path": r"\\server\share\evil.py", "content": "evil = 1\n"}) is False


def test_write_fix_duplicate_writes_second_call_overwrites():
    with tempfile.TemporaryDirectory() as project:
        fix1 = {"path": "app/utils/dup.py", "content": "value = 1\n"}
        fix2 = {"path": "app/utils/dup.py", "content": "value = 2\n"}
        assert write_fix(project, fix1) is True
        assert write_fix(project, fix2) is True
        with open(os.path.join(project, "app", "utils", "dup.py"), encoding="utf-8") as f:
            assert f.read() == "value = 2\n"


def test_write_fix_rejects_syntax_error_before_reaching_the_write():
    with tempfile.TemporaryDirectory() as project:
        result = write_fix(project, {"path": "app/routes/broken.py", "content": "x = (\n"})
        assert result is False
        assert not os.path.isfile(os.path.join(project, "app", "routes", "broken.py"))


def test_write_fix_write_failure_does_not_corrupt_existing_file():
    # Interaction with atomic_write_text's rollback: if the underlying
    # os.replace fails mid-write_fix, the previously-written file must
    # be left exactly as it was (no truncated/partial content visible).
    with tempfile.TemporaryDirectory() as project:
        path = "app/utils/target.py"
        assert write_fix(project, {"path": path, "content": "original = 1\n"}) is True

        real_replace = atomic_write_module.os.replace

        def failing_replace(*a, **k):
            raise OSError("simulated failure")

        atomic_write_module.os.replace = failing_replace
        try:
            try:
                write_fix(project, {"path": path, "content": "corrupted = True\n"})
            except OSError:
                pass
        finally:
            atomic_write_module.os.replace = real_replace

        with open(os.path.join(project, "app", "utils", "target.py"), encoding="utf-8") as f:
            content = f.read()
        assert content == "original = 1\n", (
            f"a failed write must not leave a corrupted file on disk: {content!r}"
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
