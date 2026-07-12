"""
Exp066: atomic text-file write helper, shared by write_files() and
write_fix(). Both previously wrote directly to the final path
(`open(full_path, "w")`) -- a process kill mid-write could leave a
truncated file on disk (confirmed as a repo-wide pattern in
docs/RELIABILITY_REVIEW.md's Part 4 finding). This is provably
behavior-preserving on the success path (the exact same bytes end up at
the exact same final path) and strictly safer on the crash-mid-write
path (no partial file is ever visible at the final path -- the OS-level
rename either lands the fully-written temp file or doesn't happen at
all).
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path


def atomic_write_text(path, content: str, encoding: str = "utf-8") -> None:
    """
    Write `content` to `path` atomically: write to a temp file in the
    SAME directory (guaranteeing os.replace's same-filesystem
    requirement is met), flush + fsync it, then os.replace() it onto the
    final path. os.replace() is atomic on both POSIX and Windows -- the
    final path is either the old content or the fully-written new
    content, never a partial write. The temp file is cleaned up on any
    failure before the replace.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise
