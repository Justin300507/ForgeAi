"""
Forensic Bundle System (V20.1)

A generic, versioned evidence artifact any failure class can emit —
JourneyCRUDFailure today, build/deploy/auth/vision failures later,
without a schema change. Each bundle is one JSON file under
backend/failure_memory/bundles/, cross-referenced everywhere else
(generation_log.jsonl, dashboards, experiments.md) by its failure_id.

This module deliberately knows nothing about journeys, HTTP, or any
specific failure class — callers supply stage/failure_class/step and
whatever request/response/stderr evidence they have.

Storage:
  backend/failure_memory/bundles/*.json       — one file per failure
  backend/failure_memory/failure_id_seq.json  — monotonic ID counter
"""
import json
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path

_MEM_DIR    = Path(__file__).parent.parent.parent / "failure_memory"
BUNDLE_DIR  = _MEM_DIR / "bundles"
_SEQ_PATH   = _MEM_DIR / "failure_id_seq.json"
_REPO_ROOT  = Path(__file__).parent.parent.parent.parent

# Keep in sync with backend/main.py's FastAPI(version=...) — there is no
# shared constant today (main.py's own comment says the same about its two
# copies), so this is a third copy following the same established pattern.
FORGEAI_VERSION = "19.0"

_sha_cache: list = []  # [] = not yet computed, [sha_or_None] = cached


def next_failure_id() -> str:
    """Monotonic FR-NNNNNN id, persisted across process restarts."""
    seq = {"next": 1}
    if _SEQ_PATH.exists():
        try:
            seq = json.loads(_SEQ_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    n = seq.get("next", 1)
    _MEM_DIR.mkdir(parents=True, exist_ok=True)
    _SEQ_PATH.write_text(json.dumps({"next": n + 1}), encoding="utf-8")
    return f"FR-{n:06d}"


def _git_commit_sha() -> str | None:
    if not _sha_cache:
        sha = None
        try:
            out = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=str(_REPO_ROOT), capture_output=True, text=True, timeout=3,
            )
            if out.returncode == 0:
                sha = out.stdout.strip() or None
        except Exception:
            sha = None
        _sha_cache.append(sha)
    return _sha_cache[0]


def _safe_filename_part(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", s)[:60]


def _redact_auth(value):
    """Recursively strip Authorization headers and Bearer tokens from a
    bundle payload before writing — the guarantee holds regardless of what
    a caller passes in, not just for callers that remember to pre-redact."""
    if isinstance(value, dict):
        return {
            k: _redact_auth(v) for k, v in value.items()
            if not (isinstance(k, str) and k.lower() == "authorization")
        }
    if isinstance(value, list):
        return [_redact_auth(v) for v in value]
    if isinstance(value, str) and value.startswith("Bearer "):
        return "Bearer [REDACTED]"
    return value


def write_bundle(
    *,
    project: str,
    stage: str,
    failure_class: str,
    step: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    seed: str | None = None,
    request: dict | None = None,
    response: dict | None = None,
    stderr: str | None = None,
    pipeline_version: str | None = None,
    generation: dict | None = None,
) -> dict:
    """
    Write one forensic bundle and return {"failure_id": ..., "bundle_path": ...}
    (bundle_path is relative to backend/, e.g. "failure_memory/bundles/...").
    """
    failure_id = next_failure_id()
    ts = datetime.utcnow()

    bundle = {
        "bundle_version": 1,
        "failure_id": failure_id,
        "timestamp": ts.isoformat() + "Z",
        "forgeai_version": FORGEAI_VERSION,
        "pipeline_version": pipeline_version or os.environ.get("FORGE_PIPELINE_VERSION", "v15"),
        "commit_sha": _git_commit_sha(),
        "project": project,
        "provider": provider,
        "model": model,
        "seed": seed,
        "failure": {"stage": stage, "class": failure_class, "step": step},
        "request": _redact_auth(request),
        "response": _redact_auth(response),
        "stderr": stderr[-4000:] if stderr else None,
        "generation": generation or {
            "category": None,
            "style": None,
            "layout": None,
            "design_fingerprint_id": None,
        },
        # Reserved for V20.3 (Browser Evidence) so those bundles need no
        # schema change and no migration of bundles written today.
        "artifacts": {
            "screenshot": None,
            "console_log": None,
            "network_log": None,
            "playwright_trace": None,
        },
    }

    fname = (f"{failure_id}_{ts.strftime('%Y%m%d_%H%M%S')}_"
             f"{_safe_filename_part(project)}_{failure_class.lower()}.json")
    BUNDLE_DIR.mkdir(parents=True, exist_ok=True)
    (BUNDLE_DIR / fname).write_text(
        json.dumps(bundle, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    return {"failure_id": failure_id, "bundle_path": (Path("failure_memory") / "bundles" / fname).as_posix()}
