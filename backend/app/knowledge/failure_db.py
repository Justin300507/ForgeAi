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

import ast
import hashlib
import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional


_MEM_DIR   = Path(__file__).parent.parent.parent / "failure_memory"
_CACHE_PATH = _MEM_DIR / "repair_db.json"
_LOG_PATH   = _MEM_DIR / "generation_log.jsonl"

_FUZZY_MATCH_ENABLED     = True   # master switch: compute/observe fuzzy candidates at all
_FUZZY_REPLAY_ENABLED    = False  # shadow mode until a human flips this after reviewing live telemetry
_FUZZY_MIN_SUCCESS_COUNT = 2      # a fuzzy candidate must already be proven under exact matching

# Exp133 v1: deliberately narrow. Reusing the same files already
# special-cased elsewhere in the repair loop (protected auth files,
# known-good seed stub) plus the equally boilerplate auth pages -- content
# in these files is already near-identical across unrelated generated apps,
# which is why exact-match already gets high reuse on them (see the design
# spec's repair_db.json numbers). NOT expanded to CRUD routers/models/
# middleware/components in this experiment -- that's an explicit follow-up,
# gated on this one's measured shadow-mode results.
_SCAFFOLD_ALLOWLIST = {
    "app/routes/auth_routes.py",
    "app/utils/auth.py",
    "app/routes/seed_routes.py",
    "src/pages/RegisterPage.jsx",
    "src/pages/LoginPage.jsx",
}


# ── Fix Cache ──────────────────────────────────────────────────────────────────

@dataclass
class CachedFix:
    fix_hash:     str          # SHA256 of sorted diagnostic messages
    fix_content:  dict         # {file_path: new_content, ...}
    success_count: int = 1     # times this fix was confirmed working
    first_seen:   str  = ""
    last_seen:    str  = ""
    source_idea:  str  = ""    # which app idea produced this fix
    # Exp133: composite-key near-duplicate matching, additive only --
    # existing repair_db.json entries load with these defaulted to empty,
    # and an empty composite_hash never matches a real lookup key, so old
    # entries are simply never fuzzy-eligible until re-stored.
    composite_hash:       str  = ""   # fuzzy-match index key (category|file_basename|normalized-message, hashed)
    category:             str  = ""   # informational: dominant diagnostic category for this fix
    file_basename:        str  = ""   # informational: dominant file basename for this fix
    normalized_signature: str  = ""   # informational: human-readable normalized message(s), for debugging
    files_changed:        list = field(default_factory=list)
    imports_added:        list = field(default_factory=list)  # not consumed yet -- seeds a future patch/diff-replay experiment
    symbols_added:        list = field(default_factory=list)  # not consumed yet -- seeds a future patch/diff-replay experiment


def _normalize_message(msg: str) -> str:
    """
    Strip only incidental numeric noise (line numbers, HTTP status codes,
    counts, ids) -- NEVER quoted identifiers. Exp133's proxy analysis of
    generation_log.jsonl (278 runs, 176 unique messages) found that
    stripping quoted identifiers merges failures needing different fixes
    (e.g. "AttributeError: type object 'Response'/'Donation' has no
    attribute 'create'" -- different models, different fixes). See
    docs/superpowers/specs/2026-07-21-fixcache-fuzzy-matching-design.md.
    """
    msg = re.sub(r"\d+", "<N>", msg)
    return re.sub(r"\s+", " ", msg).strip()


def _diag_field(d, name, default=None):
    """Read a field off a Diagnostic or a dict, tolerating both shapes --
    same convention _diagnostic_hash already uses."""
    if isinstance(d, dict):
        return d.get(name, default)
    return getattr(d, name, default)


def _composite_key_for_diagnostic(d) -> str:
    msg = _diag_field(d, "message", "") or ""
    cat = _diag_field(d, "category", "")
    cat_val = getattr(cat, "value", cat) or ""
    fpath = _diag_field(d, "file_path", None)
    basename = os.path.basename(fpath) if fpath else ""
    return f"{cat_val}|{basename}|{_normalize_message(msg)}"


def _composite_hash(diagnostics: list) -> str:
    """Group-level fuzzy-match key: sorted, joined per-diagnostic composite
    keys, hashed the same way _diagnostic_hash already sorts+joins raw
    messages for the exact-match key."""
    keys = sorted(_composite_key_for_diagnostic(d) for d in diagnostics)
    return hashlib.sha256("\n".join(keys).encode()).hexdigest()[:16]


def _python_import_names(content: str) -> tuple[set, set]:
    modules, symbols = set(), set()
    tree = ast.parse(content)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
            for alias in node.names:
                symbols.add(alias.name)
    return modules, symbols


def _diff_python_imports(old_content: str, new_content: str) -> tuple[list[str], list[str]]:
    old_mods, old_syms = _python_import_names(old_content)
    new_mods, new_syms = _python_import_names(new_content)
    return sorted(new_mods - old_mods), sorted(new_syms - old_syms)


_JS_IMPORT_RE = re.compile(r"import\s+(?:\{([^}]*)\}|(\w+))\s+from\s+['\"]([^'\"]+)['\"]")


def _js_import_names(content: str) -> tuple[set, set]:
    modules, symbols = set(), set()
    for m in _JS_IMPORT_RE.finditer(content):
        named, default, module = m.groups()
        modules.add(module)
        if named:
            symbols.update(s.strip().split(" as ")[0].strip() for s in named.split(",") if s.strip())
        if default:
            symbols.add(default)
    return modules, symbols


def _diff_js_imports(old_content: str, new_content: str) -> tuple[list[str], list[str]]:
    old_mods, old_syms = _js_import_names(old_content)
    new_mods, new_syms = _js_import_names(new_content)
    return sorted(new_mods - old_mods), sorted(new_syms - old_syms)


def _diff_imports(old_content: str, new_content: str, rel_path: str) -> tuple[list[str], list[str]]:
    """
    Best-effort: imports/symbols a fix introduced, vs the pre-fix file.
    NOT consumed by any matching logic in Exp133 -- stored purely to seed a
    future move from whole-file replay to patch/diff replay. Any parse
    failure yields empty lists; never raises.
    """
    try:
        if rel_path.endswith(".py"):
            return _diff_python_imports(old_content, new_content)
        if rel_path.endswith((".jsx", ".js", ".tsx", ".ts")):
            return _diff_js_imports(old_content, new_content)
    except Exception:
        pass
    return [], []


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

# Same intent as _GENERIC_MESSAGES, but for message FAMILIES where even the
# "detailed" variant still carries no project-specific signal. engine.py
# appends the failing step name to a JourneyCRUDFailure message (e.g.
# "... — Edit entity: 422"), which defeats the exact-string check above (it
# no longer matches _GENERIC_MESSAGES verbatim) while remaining just as
# collision-prone: "Edit entity: 422" says nothing about WHICH field/schema
# actually caused the 422, so any two unrelated apps whose edit step happens
# to fail the same way hash to the identical hit. Confirmed live
# (habit_tracker, 2026-07-30): FixCache replayed a cached fix that created
# app/routes/product_routes.py / a ProductPage / a schemas/product.py stub
# into a habit tracker -- a completely unrelated resource from whatever
# earlier project actually had a Product model -- burning 2 of 5 fix
# attempts on a guaranteed-wrong fix before the loop finally gave up on the
# cache and diagnosed the real problem itself.
_GENERIC_MESSAGE_PREFIXES = (
    "[JourneyCRUDFailure] Backend healthy but CRUD journey failed",
)


def _is_cacheable(diagnostics: list) -> bool:
    """False if every diagnostic in the set carries a known contentless message
    (exact match) or belongs to a known-collision-prone message family
    (prefix match)."""
    for d in diagnostics:
        msg = d.get("message", str(d)) if isinstance(d, dict) else getattr(d, "message", str(d))
        if msg in _GENERIC_MESSAGES:
            continue
        if msg.startswith(_GENERIC_MESSAGE_PREFIXES):
            continue
        return True
    return False


def _normalized_path(p: str) -> str:
    return p.replace("\\", "/").lstrip("/")


def _is_fuzzy_eligible(diagnostics: list) -> bool:
    """
    Fail closed: every diagnostic in the group must be either a known
    scaffold file or a file-less IMPORT-cascade diagnostic. One ineligible
    diagnostic disqualifies the whole group -- no partial matching.
    """
    if not diagnostics or not _is_cacheable(diagnostics):
        return False
    for d in diagnostics:
        fpath = _diag_field(d, "file_path", None)
        if fpath:
            if _normalized_path(fpath) not in _SCAFFOLD_ALLOWLIST:
                return False
        else:
            cat = _diag_field(d, "category", None)
            cat_val = getattr(cat, "value", cat)
            if cat_val != "import":
                return False
    return True


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
        self._rebuild_normalized_index()

    def _rebuild_normalized_index(self):
        self._normalized_index: dict[str, list[str]] = {}
        for fix_hash, entry in self._data.items():
            chash = entry.get("composite_hash")
            if chash:
                self._normalized_index.setdefault(chash, []).append(fix_hash)

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

    def evict(self, diagnostics: list) -> bool:
        """
        Exp156 (habit_tracker, 2026-07-31): a cached fix stored before the
        "only cache on confirmed success" gate existed (or one whose
        context-dependent content happens to regress in a DIFFERENT
        project than it was proven in) gets replayed verbatim forever --
        `lookup()` has no expiry and `store()` only ever overwrites with a
        newer fix, never removes a bad one. Confirmed live: `app/schemas/
        stats.py`'s cached fix contained `Field(None, min_length=1,
        default=None)` -- `default` supplied both positionally and by
        keyword, a hard TypeError at import time -- and every single
        repair attempt replayed it via cache HIT, regressed, reverted,
        then replayed the identical broken content again next attempt.
        Same "stuck forever, 3 attempts, no improvement" shape as
        Exp146's oscillation, different mechanism: no LLM re-guessing, a
        cache that never forgets a mistake.

        Call when a group's cached fix is confirmed to have caused (or
        coincided with) a reverted regression, so the NEXT occurrence of
        this exact diagnostic pattern goes to a fresh LLM call instead of
        replaying the same proven-bad content. Returns True if an entry
        was actually removed.
        """
        if not diagnostics or not _is_cacheable(diagnostics):
            return False
        h = _diagnostic_hash(diagnostics)
        if h not in self._data:
            return False
        del self._data[h]
        self._rebuild_normalized_index()
        self._save()
        return True

    def lookup_fuzzy(self, diagnostics: list) -> Optional[CachedFix]:
        """
        Second-tier lookup, meant to be consulted only after an exact
        lookup() miss. Fails closed via _is_fuzzy_eligible: only fires for
        the scaffold-file allowlist or file-less IMPORT cascades, and only
        for entries already proven (success_count >= _FUZZY_MIN_SUCCESS_COUNT).
        """
        if not _FUZZY_MATCH_ENABLED:
            return None
        if not _is_fuzzy_eligible(diagnostics):
            return None
        chash = _composite_hash(diagnostics)
        best = None
        for fix_hash in self._normalized_index.get(chash, []):
            entry = self._data.get(fix_hash)
            if not entry:
                continue
            try:
                cf = CachedFix(**entry)
            except Exception:
                continue
            if cf.success_count < _FUZZY_MIN_SUCCESS_COUNT:
                continue
            if best is None or cf.success_count > best.success_count:
                best = cf
        return best

    def store(self, diagnostics: list, fix_content: dict, idea: str = "",
              pre_fix_content: Optional[dict] = None) -> str:
        """
        Record a successful fix for this failure pattern.
        Returns the hash key.
        """
        if not diagnostics or not _is_cacheable(diagnostics):
            return ""
        h = _diagnostic_hash(diagnostics)
        chash = _composite_hash(diagnostics)
        cat_raw = _diag_field(diagnostics[0], "category", "")
        cat_val = getattr(cat_raw, "value", cat_raw) or ""
        first_file = next(
            (_diag_field(d, "file_path", None) for d in diagnostics if _diag_field(d, "file_path", None)),
            None,
        )
        basename = os.path.basename(first_file) if first_file else ""
        signature = " | ".join(sorted(
            _normalize_message(_diag_field(d, "message", "") or "") for d in diagnostics
        ))
        imports_added, symbols_added = set(), set()
        if pre_fix_content:
            for rel_path, new_content in fix_content.items():
                old_content = pre_fix_content.get(rel_path)
                if old_content is None:
                    continue
                added_mods, added_syms = _diff_imports(old_content, new_content, rel_path)
                imports_added.update(added_mods)
                symbols_added.update(added_syms)
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        existing = self._data.get(h)
        if existing:
            existing["success_count"] = existing.get("success_count", 0) + 1
            existing["last_seen"] = ts
            existing["fix_content"] = fix_content  # update to latest working fix
            existing["composite_hash"] = chash
            existing["category"] = cat_val
            existing["file_basename"] = basename
            existing["normalized_signature"] = signature
            existing["files_changed"] = list(fix_content.keys())
            existing["imports_added"] = sorted(imports_added)
            existing["symbols_added"] = sorted(symbols_added)
        else:
            self._data[h] = {
                "fix_hash":      h,
                "fix_content":   fix_content,
                "success_count": 1,
                "first_seen":    ts,
                "last_seen":     ts,
                "source_idea":   idea[:100],
                "composite_hash": chash,
                "category": cat_val,
                "file_basename": basename,
                "normalized_signature": signature,
                "files_changed": list(fix_content.keys()),
                "imports_added": sorted(imports_added),
                "symbols_added": sorted(symbols_added),
            }
        self._save()
        self._rebuild_normalized_index()
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
    # Count of fix attempts where ctx.detect_regression() found new
    # diagnostics with no offsetting score gain (Events.REGRESSION fires
    # live for this but was never persisted for historical querying --
    # Observatory's "Regression Alerts" reads this). 0 for pre-existing lines.
    regression_count: int = 0
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
