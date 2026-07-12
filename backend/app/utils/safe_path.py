r"""
Exp066: centralized path-traversal guard, shared by every writer that
places an LLM-controlled relative path under a project root
(app/services/file_writer_service.py::write_files -- the initial
generation writer, confirmed in Exp065 to have NO path validation at
all -- and app/services/fix_writer_service.py::write_fix -- the
repair-time writer, which already had a narrower inline check this
module supersedes with an identical-or-stricter one).

Uses pathlib exclusively, no regex -- per this experiment's own
requirement. Rejects, in order:
  - `../`-style relative traversal (via resolved-path containment, not
    string matching -- catches "a/../../b" and other multi-segment
    forms a naive `startswith("..")` check on the raw string can miss)
  - absolute paths (POSIX or the current OS's own absolute-path rules)
  - Windows drive-letter and UNC paths (checked explicitly via
    PureWindowsPath, so this catches `C:\...` / `\\server\share\...`
    payloads regardless of which OS ForgeAI's own process happens to
    run on -- a Linux-hosted deployment should still reject a literal
    Windows-drive-shaped string, since it's unambiguously not a
    legitimate relative project path)
  - symlink escapes (Path.resolve() follows any symlinks that exist
    along the path before the containment check runs)
"""
from __future__ import annotations

from pathlib import Path, PureWindowsPath


class PathTraversalError(ValueError):
    """Raised when a candidate relative path would escape the project root."""


def has_windows_drive_or_unc(candidate_path: str) -> bool:
    """
    True for a drive-letter absolute/relative path (`C:\\foo`, `C:foo`)
    or a UNC path (`\\\\server\\share\\foo`) -- checked via
    PureWindowsPath.drive, which is populated for both shapes. This is a
    pure, filesystem-free check (PureWindowsPath never touches disk), so
    it runs identically regardless of the host OS -- a Linux-hosted
    ForgeAI process still rejects a literal Windows-drive-shaped string,
    since POSIX would otherwise happily treat "C:\\foo" as a single
    oddly-named path component and let it through.

    Public (Exp066 named this `_has_windows_drive_or_unc`; Exp067 made
    it public) -- reused as a standalone defense-in-depth check by
    app/repair/orchestrator.py::_regenerate_module, which has its own
    separate path-validation function (_safe_patch_target) this module
    does not replace (see docs/WRITE_VALIDATION_MATRIX.md for why).
    """
    pw = PureWindowsPath(candidate_path)
    return bool(pw.drive)


def resolve_safe_path(base_dir, candidate_path: str) -> Path:
    """
    Resolve `candidate_path` (a relative path from LLM-controlled input)
    against `base_dir`, returning the resolved absolute Path if -- and
    only if -- it lands inside `base_dir`. Raises PathTraversalError
    otherwise. Does not require the target file to already exist (a new
    file legitimately won't), but DOES resolve any symlinks that exist
    along the way, so a symlink planted inside the project root pointing
    outside it is still caught.
    """
    if not candidate_path or not str(candidate_path).strip():
        raise PathTraversalError("empty path")

    base = Path(base_dir).resolve()
    candidate_str = str(candidate_path)

    if has_windows_drive_or_unc(candidate_str):
        raise PathTraversalError(f"Windows drive/UNC path rejected: {candidate_path!r}")

    candidate = Path(candidate_str)
    if candidate.is_absolute():
        raise PathTraversalError(f"absolute path rejected: {candidate_path!r}")

    resolved = (base / candidate).resolve()

    try:
        resolved.relative_to(base)
    except ValueError:
        raise PathTraversalError(
            f"path escapes project root: {candidate_path!r} -> resolved to {resolved}, "
            f"which is not inside {base}"
        )

    return resolved


def is_safe_path(base_dir, candidate_path: str) -> bool:
    """Boolean convenience wrapper -- True if resolve_safe_path would succeed."""
    try:
        resolve_safe_path(base_dir, candidate_path)
        return True
    except PathTraversalError:
        return False
