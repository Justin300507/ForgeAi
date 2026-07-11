"""
Failure Database + Fix Cache

Two persistent stores backed by JSON (consistent with existing failure_memory/ pattern).

1. FAILURE DATABASE — every generation attempt is recorded:
   {idea, architecture, diagnostics, fixes_applied, final_score, succeeded}

   Over time this becomes thousands of solved engineering problems.
   It answers: "what kind of apps fail most often? what fixes work?"

2. FIX CACHE — hash(diagnostics) → successful fix that resolved them:
   When the same failure pattern appears again, replay the fix directly.
   No LLM call needed. Saves tokens + latency.

Storage:
  backend/failure_memory/repair_db.json    — fix cache (hash → fix)
  backend/failure_memory/generation_log.jsonl  — full attempt log (append-only)
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional


_MEM_DIR   = Path(__file__).parent.parent.parent / "failure_memory"
_CACHE_PATH = _MEM_DIR / "repair_db.json"
_LOG_PATH   = _MEM_DIR / "generation_log.jsonl"


# ── Fix Cache ──────────────────────────────────────────────────────────────────

@dataclass
class CachedFix:
    fix_hash:     str          # SHA256 of sorted diagnostic messages
    fix_content:  dict         # {file_path: new_content, ...}
    success_count: int = 1     # times this fix was confirmed working
    first_seen:   str  = ""
    last_seen:    str  = ""
    source_idea:  str  = ""    # which app idea produced this fix


def _diagnostic_hash(diagnostics: list) -> str:
    """Stable hash of a diagnostic set. Order-independent."""
    msgs = sorted(
        d.get("message", str(d)) if isinstance(d, dict) else getattr(d, "message", str(d))
        for d in diagnostics
    )
    return hashlib.sha256("\n".join(msgs).encode()).hexdigest()[:16]


# Messages carrying zero project-specific signal. If a diagnostic message is
# exactly one of these (verbatim, no extra detail appended), it's identical
# across every unrelated project that happens to hit the same generic
# fallback path — e.g. app/verification/engine.py falling back to
# "Backend failed to start" when the real crash detail wasn't captured.
# Hashing purely on message text would then make FixCache.lookup() return a
# HIT for a totally different underlying bug, silently replaying a stale,
# irrelevant patch instead of asking the LLM to diagnose the real problem.
# Skip the cache entirely for these so every occurrence gets a fresh look.
_GENERIC_MESSAGES = {
    "[RuntimeError] Backend failed to start",
    "[JourneyCRUDFailure] Backend healthy but CRUD journey failed",
}


def _is_cacheable(diagnostics: list) -> bool:
    """False if every diagnostic in the set carries a known contentless message."""
    for d in diagnostics:
        msg = d.get("message", str(d)) if isinstance(d, dict) else getattr(d, "message", str(d))
        if msg not in _GENERIC_MESSAGES:
            return True
    return False


class FixCache:
    """
    Hash-based lookup: same failure pattern → reuse the fix that worked before.

    Usage:
        cache = FixCache()
        hit = cache.lookup(diagnostics)
        if hit:
            apply_fix_content(hit.fix_content)
        else:
            fix = llm_generate_fix(...)
            cache.store(diagnostics, fix)
    """

    def __init__(self):
        self._data: dict[str, dict] = {}  # hash → serialized CachedFix
        self._load()

    def _load(self):
        if _CACHE_PATH.exists():
            try:
                self._data = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
            except Exception:
                self._data = {}

    def _save(self):
        _MEM_DIR.mkdir(parents=True, exist_ok=True)
        _CACHE_PATH.write_text(json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8")

    def lookup(self, diagnostics: list) -> Optional[CachedFix]:
        """Return cached fix if we've seen this failure pattern before."""
        if not diagnostics or not _is_cacheable(diagnostics):
            return None
        h = _diagnostic_hash(diagnostics)
        entry = self._data.get(h)
        if not entry:
            return None
        try:
            return CachedFix(**entry)
        except Exception:
            return None

    def store(self, diagnostics: list, fix_content: dict, idea: str = "") -> str:
        """
        Record a successful fix for this failure pattern.
        Returns the hash key.
        """
        if not diagnostics or not _is_cacheable(diagnostics):
            return ""
        h = _diagnostic_hash(diagnostics)
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        existing = self._data.get(h)
        if existing:
            existing["success_count"] = existing.get("success_count", 0) + 1
            existing["last_seen"] = ts
            existing["fix_content"] = fix_content  # update to latest working fix
        else:
            self._data[h] = {
                "fix_hash":      h,
                "fix_content":   fix_content,
                "success_count": 1,
                "first_seen":    ts,
                "last_seen":     ts,
                "source_idea":   idea[:100],
            }
        self._save()
        return h

    def hit_rate_summary(self) -> dict:
        total = sum(e.get("success_count", 1) for e in self._data.values())
        return {
            "unique_patterns": len(self._data),
            "total_cache_hits": total,
            "top_patterns": sorted(
                self._data.values(),
                key=lambda e: e.get("success_count", 0),
                reverse=True,
            )[:5],
        }


# ── Generation Log ─────────────────────────────────────────────────────────────

@dataclass
class GenerationRecord:
    idea:             str
    attempt_number:   int
    final_score:      float
    succeeded:        bool          # score >= DEPLOY_THRESHOLD
    fix_count:        int
    dominant_errors:  list[str]     # top error messages
    architecture_hash: str          # sha256 of project structure (for "similar apps" lookup)
    bundle_refs:      list = field(default_factory=list)  # [{"failure_id", "bundle_path"}, ...]
    # {patcher_or_stage_name: count} -- Deterministic Prevention Rate raw
    # data (see reliability_metrics.py). Defaults to {} for backward
    # compatibility with pre-existing generation_log.jsonl lines.
    prevention_counts: dict = field(default_factory=dict)
    timestamp:        str = ""


class GenerationLog:
    """
    Append-only log of every generation attempt.

    Used by the confidence engine to answer:
    - "Of apps with similar characteristics, what % succeeded?"
    - "What's the historical success rate?"
    - "What fix count typically correlates with success?"
    """

    def record(self, rec: GenerationRecord):
        rec.timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        _MEM_DIR.mkdir(parents=True, exist_ok=True)
        with _LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(rec)) + "\n")

    def load_recent(self, n: int = 200) -> list[GenerationRecord]:
        if not _LOG_PATH.exists():
            return []
        lines = _LOG_PATH.read_text(encoding="utf-8").splitlines()[-n:]
        records = []
        for line in lines:
            try:
                records.append(GenerationRecord(**json.loads(line)))
            except Exception:
                pass
        return records

    def success_rate(self, recent_n: int = 100) -> float:
        records = self.load_recent(recent_n)
        if not records:
            return 0.75  # conservative prior with no data
        return sum(1 for r in records if r.succeeded) / len(records)

    def success_rate_by_fix_count(self, fix_count: int, recent_n: int = 200) -> Optional[float]:
        records = self.load_recent(recent_n)
        bucket = [r for r in records if r.fix_count == fix_count]
        if len(bucket) < 3:  # not enough data
            return None
        return sum(1 for r in bucket if r.succeeded) / len(bucket)

    def stats(self) -> dict:
        records = self.load_recent(500)
        if not records:
            return {"total": 0, "success_rate": 0.0, "avg_score": 0.0, "avg_fixes": 0.0}
        return {
            "total":        len(records),
            "success_rate": round(sum(1 for r in records if r.succeeded) / len(records) * 100, 1),
            "avg_score":    round(sum(r.final_score for r in records) / len(records), 1),
            "avg_fixes":    round(sum(r.fix_count for r in records) / len(records), 1),
        }


# ── Singletons ─────────────────────────────────────────────────────────────────

fix_cache   = FixCache()
generation_log = GenerationLog()
