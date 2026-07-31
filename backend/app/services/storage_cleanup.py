"""
Automatic disk-space guard for the persistent /data volume (Exp161,
2026-08-01). generated_projects/ node_modules trees (each ~50-150MB)
accumulate on Railway's persistent volume across every generation ever
run, and nothing cleaned them up automatically -- confirmed live twice
in one day (the volume sat at 467.8/500MB, 93.6% full, both times, and
sweeping stale node_modules alone freed 200-250MB each time). Before
this, the only fix was the manual admin-only /admin/clean-node-modules
emergency endpoint. This module is what that endpoint now delegates to,
plus a proactive guard called automatically at the start of every new
job so the volume never has to reach the disk-full write-rejection
state (sqlite3.OperationalError: database or disk is full) again.
"""
from __future__ import annotations

import os
import shutil


def clean_stale_node_modules(root: str | None = None, exclude_project: str | None = None) -> dict:
    """Deletes every node_modules/ directory under generated_projects/,
    except (optionally) one belonging to `exclude_project` -- a job
    currently in flight reuses its own node_modules across repair-loop
    iterations for speed ("node_modules exists -- skipping npm install"),
    so an in-progress project must never be swept out from under itself.
    node_modules is always regenerable via a fresh `npm install`; the
    already-built .zip export for a project is a separate static
    artifact this never touches.
    """
    if root is None:
        root = "/data/generated_projects" if os.path.isdir("/data/generated_projects") else "generated_projects"

    freed = 0
    removed: list[str] = []
    if os.path.isdir(root):
        for dirpath, dirnames, _filenames in os.walk(root):
            if exclude_project and os.path.basename(dirpath) == exclude_project:
                dirnames[:] = []  # don't descend into the in-flight project at all
                continue
            if "node_modules" in dirnames:
                target = os.path.join(dirpath, "node_modules")
                try:
                    size = sum(
                        os.path.getsize(os.path.join(dp, f))
                        for dp, _dn, fn in os.walk(target) for f in fn
                        if os.path.exists(os.path.join(dp, f))
                    )
                    shutil.rmtree(target)
                    freed += size
                    removed.append(target)
                except Exception as exc:
                    removed.append(f"{target} (failed: {exc})")
                dirnames.remove("node_modules")

    disk_root = "/data" if os.path.isdir("/data") else "."
    disk = shutil.disk_usage(disk_root)
    return {
        "root": root,
        "removed_count": len(removed),
        "removed": removed[:50],
        "bytes_freed": freed,
        "disk_free_bytes_after": disk.free,
    }


def disk_usage_ratio(path: str | None = None) -> float:
    """Fraction of the volume currently used, e.g. 0.936 for 93.6%."""
    disk = shutil.disk_usage(path or ("/data" if os.path.isdir("/data") else "."))
    if disk.total == 0:
        return 0.0
    return disk.used / disk.total


# Sweep proactively once usage crosses this fraction -- picked below the
# ~93.6% level that twice produced sqlite3.OperationalError: database or
# disk is full on plain writes (registration, job inserts), so the guard
# fires with real headroom left to actually complete the sweep and the
# next job's own writes, not exactly at the failure line.
DISK_CLEANUP_THRESHOLD = 0.80


def clean_stale_node_modules_if_needed(exclude_project: str | None = None) -> dict | None:
    """Runs the sweep only when the volume is actually getting tight, so
    a normal job doesn't pay an unnecessary npm reinstall cost for some
    other, unrelated project it happens to run after. Never raises --
    a storage-guard failure must not block the job it's guarding.
    Returns None when there's plenty of room or the check itself fails.
    """
    try:
        if disk_usage_ratio() < DISK_CLEANUP_THRESHOLD:
            return None
        result = clean_stale_node_modules(exclude_project=exclude_project)
    except Exception as exc:
        print(f"  [storage_guard] check/sweep failed (non-fatal): {exc}")
        return None
    if result["removed_count"]:
        print(
            f"  [storage_guard] Volume usage crossed {DISK_CLEANUP_THRESHOLD:.0%} -- "
            f"swept {result['removed_count']} stale node_modules tree(s), "
            f"freed {result['bytes_freed'] / 1024 / 1024:.1f}MB"
        )
    return result
