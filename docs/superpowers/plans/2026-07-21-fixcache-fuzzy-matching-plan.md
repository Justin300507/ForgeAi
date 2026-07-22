# Exp133 FixCache Fuzzy Matching (Shadow Mode) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a second, composite-key ("category + file basename + digit-normalized message") lookup tier to `FixCache`, gated to a small scaffold-file allowlist and `success_count >= 2`, running in **shadow mode only** — it observes and logs what it would have matched but never applies a fuzzy fix until a human flips `_FUZZY_REPLAY_ENABLED` after reviewing live telemetry.

**Architecture:** Purely additive changes to `backend/app/knowledge/failure_db.py` (data model, normalization, composite index, `lookup_fuzzy()`) and `backend/app/repair/orchestrator.py` (a new `_try_cache_fix()` helper that both the exact and fuzzy tiers now go through, wired into `_apply_fix_group()`). The existing exact-match cache path, `grouper.py` clustering, and the snapshot/revert regression safety net are all untouched.

**Tech Stack:** Python 3.14, stdlib only (`hashlib`, `re`, `ast`, `json`, `dataclasses`) — no new dependencies.

## Global Constraints

- The design spec is frozen: `docs/superpowers/specs/2026-07-21-fixcache-fuzzy-matching-design.md`. Do not deviate from its algorithm (composite key = category + file_basename + digit-only-normalized message; scaffold allowlist; `success_count >= 2`) without stopping to re-confirm with the user.
- **`_FUZZY_REPLAY_ENABLED` must remain `False` at the end of this plan.** This plan builds and validates the shadow-mode observation path only. Flipping it to actually replay fuzzy fixes is an explicit, separate, human decision made after reviewing live telemetry from real generation runs — never part of this implementation.
- All new tests are plain `test_*` functions in `backend/tests/reliability/test_exp133_fuzzy_fix_cache.py`, following the existing convention (see `test_exp081_strategy_memory_versioning.py`): isolated via monkeypatched module-level path/flag constants restored in a `finally`, collected and run via a `__main__` block, zero network calls.
- Do not touch the fixed 3-app canary composition (todo / blog_cms / crm) — canary changes require separate explicit sign-off per `CLAUDE.md`.
- Every new field on `CachedFix` must have a default so the existing 252-entry `repair_db.json` loads unchanged (verified in Task 1).

---

### Task 1: Data model — extend `CachedFix`, verify backward compatibility

**Files:**
- Modify: `backend/app/knowledge/failure_db.py:37-44` (the `CachedFix` dataclass)
- Test: `backend/tests/reliability/test_exp133_fuzzy_fix_cache.py` (new file)

**Interfaces:**
- Produces: `CachedFix` with new optional fields `composite_hash: str`, `category: str`, `file_basename: str`, `normalized_signature: str`, `files_changed: list`, `imports_added: list`, `symbols_added: list` — all default to `""`/`[]`, consumed by Tasks 3-6.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/reliability/test_exp133_fuzzy_fix_cache.py`:

```python
"""
Experiment 133 (FixCache Fuzzy Matching, shadow mode): regression tests for
the composite-key near-duplicate lookup tier added to
app/knowledge/failure_db.py and its shadow-mode wiring in
app/repair/orchestrator.py.

See docs/superpowers/specs/2026-07-21-fixcache-fuzzy-matching-design.md for
the full design and the proxy-analysis evidence behind the "strip only
digits, never quoted identifiers" normalization rule enforced below.

Run directly: python tests/reliability/test_exp133_fuzzy_fix_cache.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import app.knowledge.failure_db as fdb
from app.core.context import Diagnostic, ErrorCategory, ErrorSeverity


def _isolated_cache(tmpdir: str) -> Path:
    """Point FixCache's storage path at a throwaway file for this test, and
    rebuild the singleton's state so no other test's data leaks in."""
    path = Path(tmpdir) / "repair_db.json"
    fdb._CACHE_PATH = path
    return path


def test_old_entries_load_without_migration():
    with tempfile.TemporaryDirectory() as td:
        cache_path = _isolated_cache(td)
        old_entry = {
            "fix_hash": "abc123",
            "fix_content": {"app/utils/auth.py": "old content"},
            "success_count": 3,
            "first_seen": "2026-01-01T00:00:00Z",
            "last_seen": "2026-01-02T00:00:00Z",
            "source_idea": "todo app",
        }
        cache_path.write_text(json.dumps({"abc123": old_entry}), encoding="utf-8")
        cache = fdb.FixCache()
        cf = fdb.CachedFix(**cache._data["abc123"])
        assert cf.success_count == 3
        assert cf.fix_content == {"app/utils/auth.py": "old content"}
        # New Exp133 fields must default cleanly for pre-existing entries
        assert cf.category == ""
        assert cf.file_basename == ""
        assert cf.composite_hash == ""
        assert cf.normalized_signature == ""
        assert cf.files_changed == []
        assert cf.imports_added == []
        assert cf.symbols_added == []


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python tests/reliability/test_exp133_fuzzy_fix_cache.py`
Expected: `ERROR: test_old_entries_load_without_migration` — `TypeError: CachedFix.__init__() got an unexpected keyword argument` is not the failure here (the entry has no new keys yet); the real expected failure is `AttributeError: 'CachedFix' object has no attribute 'category'` since the field doesn't exist yet.

- [ ] **Step 3: Add the new fields to `CachedFix`**

In `backend/app/knowledge/failure_db.py`, replace the `CachedFix` dataclass (lines 37-44):

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python tests/reliability/test_exp133_fuzzy_fix_cache.py`
Expected: `PASS: test_old_entries_load_without_migration` and `1/1 passed`

- [ ] **Step 5: Commit**

```bash
git add backend/app/knowledge/failure_db.py backend/tests/reliability/test_exp133_fuzzy_fix_cache.py
git commit -m "Exp133 (1/8): add optional composite-key fields to CachedFix"
```

---

### Task 2: Normalization — digits only, proxy-analysis regression tests

**Files:**
- Modify: `backend/app/knowledge/failure_db.py` (add `import re`, `import os` near the top; add `_normalize_message`)
- Test: `backend/tests/reliability/test_exp133_fuzzy_fix_cache.py`

**Interfaces:**
- Consumes: nothing new
- Produces: `_normalize_message(msg: str) -> str`, used by Task 3's composite key.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/reliability/test_exp133_fuzzy_fix_cache.py`, above the `if __name__ == "__main__":` block:

```python
def test_normalization_strips_only_digits():
    assert fdb._normalize_message("Edit entity: 405") == "Edit entity: <N>"
    assert fdb._normalize_message("Edit entity: 422") == "Edit entity: <N>"
    assert fdb._normalize_message("Transform failed with 6 errors") == \
        fdb._normalize_message("Transform failed with 9 errors")


def test_normalization_preserves_dangerous_identifiers():
    # Exp133 proxy analysis (generation_log.jsonl, 278 runs): stripping quoted
    # identifiers merged 3 of 5 collisions into failures needing DIFFERENT
    # fixes. These two must never normalize to the same string.
    a = fdb._normalize_message(
        "[AttributeError] AttributeError: type object 'Response' has no attribute 'create'")
    b = fdb._normalize_message(
        "[AttributeError] AttributeError: type object 'Donation' has no attribute 'create'")
    assert a != b, "quoted class names must remain distinguishable"

    c = fdb._normalize_message("Undefined symbol 'User' in app/routes/stats_routes.py")
    d = fdb._normalize_message("Undefined symbol 'func' in app/routes/stats_routes.py")
    assert c != d, "quoted symbol names must remain distinguishable"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python tests/reliability/test_exp133_fuzzy_fix_cache.py`
Expected: `ERROR: test_normalization_strips_only_digits: AttributeError: module 'app.knowledge.failure_db' has no attribute '_normalize_message'` (and the same for the second test)

- [ ] **Step 3: Implement `_normalize_message`**

In `backend/app/knowledge/failure_db.py`, add to the imports at the top (currently `hashlib`, `json`, `time`, `dataclasses`, `pathlib`, `typing`):

```python
import os
import re
```

Then add, just above the existing `_diagnostic_hash` function:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python tests/reliability/test_exp133_fuzzy_fix_cache.py`
Expected: both new tests `PASS`, `3/3 passed`

- [ ] **Step 5: Commit**

```bash
git add backend/app/knowledge/failure_db.py backend/tests/reliability/test_exp133_fuzzy_fix_cache.py
git commit -m "Exp133 (2/8): digit-only normalization, proxy-analysis regression tests"
```

---

### Task 3: Composite-key index — build it, don't call it yet

**Files:**
- Modify: `backend/app/knowledge/failure_db.py` (add `_diag_field`, `_composite_key_for_diagnostic`, `_composite_hash`; extend `FixCache._load()`/`store()` to build/persist the index)
- Test: `backend/tests/reliability/test_exp133_fuzzy_fix_cache.py`

**Interfaces:**
- Consumes: `_normalize_message` (Task 2)
- Produces: `_composite_hash(diagnostics: list) -> str`, `FixCache._normalized_index: dict[str, list[str]]` (composite_hash → [fix_hash, ...]), populated by `store()` and rebuilt on `_load()`. Not yet read by any lookup method — that's Task 4.

- [ ] **Step 1: Write the failing tests**

Add to the test file:

```python
def test_composite_key_distinguishes_undefined_symbols():
    d1 = Diagnostic(error_id="1", category=ErrorCategory.RUNTIME, severity=ErrorSeverity.HIGH,
                     source="static", message="Undefined symbol 'User' in app/routes/stats_routes.py",
                     file_path="app/routes/stats_routes.py")
    d2 = Diagnostic(error_id="2", category=ErrorCategory.RUNTIME, severity=ErrorSeverity.HIGH,
                     source="static", message="Undefined symbol 'func' in app/routes/stats_routes.py",
                     file_path="app/routes/stats_routes.py")
    assert fdb._composite_key_for_diagnostic(d1) != fdb._composite_key_for_diagnostic(d2)


def test_composite_key_merges_benign_numeric_variation():
    d1 = Diagnostic(error_id="1", category=ErrorCategory.API, severity=ErrorSeverity.MEDIUM,
                     source="runtime", file_path="app/routes/auth_routes.py",
                     message="[JourneyCRUDFailure] Backend healthy but CRUD journey failed -- Edit entity: 405")
    d2 = Diagnostic(error_id="2", category=ErrorCategory.API, severity=ErrorSeverity.MEDIUM,
                     source="runtime", file_path="app/routes/auth_routes.py",
                     message="[JourneyCRUDFailure] Backend healthy but CRUD journey failed -- Edit entity: 422")
    assert fdb._composite_key_for_diagnostic(d1) == fdb._composite_key_for_diagnostic(d2)


def test_store_persists_and_index_finds_composite_hash():
    with tempfile.TemporaryDirectory() as td:
        _isolated_cache(td)
        cache = fdb.FixCache()
        diags = [Diagnostic(error_id="1", category=ErrorCategory.IMPORT, severity=ErrorSeverity.CRITICAL,
                             source="static", message="No module named 'jwt'")]
        h = cache.store(diags, {"app/utils/auth.py": "import jwt"}, idea="test app")
        chash = fdb._composite_hash(diags)
        assert chash in cache._normalized_index
        assert h in cache._normalized_index[chash]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python tests/reliability/test_exp133_fuzzy_fix_cache.py`
Expected: `ERROR ... AttributeError: module 'app.knowledge.failure_db' has no attribute '_composite_key_for_diagnostic'` (and similarly for `_composite_hash` / `_normalized_index`)

- [ ] **Step 3: Implement the composite key and index**

In `backend/app/knowledge/failure_db.py`, add just below `_normalize_message`:

```python
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
```

Then update `FixCache`. Replace the `_load` method:

```python
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
```

And update `store()` to compute and persist the new fields (replace the whole method):

```python
    def store(self, diagnostics: list, fix_content: dict, idea: str = "") -> str:
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
                "imports_added": [],
                "symbols_added": [],
            }
        self._save()
        self._rebuild_normalized_index()
        return h
```

(`imports_added`/`symbols_added` populated properly in Task 6; left as empty lists here so `store()` is already fully valid on its own.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python tests/reliability/test_exp133_fuzzy_fix_cache.py`
Expected: all tests `PASS`, `6/6 passed`

- [ ] **Step 5: Commit**

```bash
git add backend/app/knowledge/failure_db.py backend/tests/reliability/test_exp133_fuzzy_fix_cache.py
git commit -m "Exp133 (3/8): composite-key index built on store/load, not yet queried"
```

---

### Task 4: `lookup_fuzzy()` behind `_FUZZY_MATCH_ENABLED`

**Files:**
- Modify: `backend/app/knowledge/failure_db.py` (add flags, `lookup_fuzzy()`)
- Test: `backend/tests/reliability/test_exp133_fuzzy_fix_cache.py`

**Interfaces:**
- Consumes: `_composite_hash` (Task 3), `FixCache._normalized_index` (Task 3)
- Produces: `FixCache.lookup_fuzzy(diagnostics: list) -> Optional[CachedFix]`; module constants `_FUZZY_MATCH_ENABLED`, `_FUZZY_REPLAY_ENABLED`, `_FUZZY_MIN_SUCCESS_COUNT`. Consumed by Task 5 (eligibility) and Task 7 (orchestrator wiring).

- [ ] **Step 1: Write the failing tests**

Add to the test file:

```python
def test_lookup_fuzzy_requires_min_success_count():
    with tempfile.TemporaryDirectory() as td:
        _isolated_cache(td)
        cache = fdb.FixCache()
        diags = [Diagnostic(error_id="1", category=ErrorCategory.IMPORT, severity=ErrorSeverity.CRITICAL,
                             source="static", message="No module named 'jwt'",
                             file_path="app/utils/auth.py")]
        cache.store(diags, {"app/utils/auth.py": "import jwt"}, idea="app one")
        # Only stored once -- success_count == 1, below the fuzzy threshold
        same_shape = [Diagnostic(error_id="2", category=ErrorCategory.IMPORT, severity=ErrorSeverity.CRITICAL,
                                  source="static", message="No module named 'jwt'",
                                  file_path="app/utils/auth.py")]
        assert cache.lookup_fuzzy(same_shape) is None

        cache.store(diags, {"app/utils/auth.py": "import jwt"}, idea="app two")  # success_count -> 2
        hit = cache.lookup_fuzzy(same_shape)
        assert hit is not None
        assert hit.success_count == 2


def test_lookup_fuzzy_disabled_returns_none():
    with tempfile.TemporaryDirectory() as td:
        _isolated_cache(td)
        cache = fdb.FixCache()
        diags = [Diagnostic(error_id="1", category=ErrorCategory.IMPORT, severity=ErrorSeverity.CRITICAL,
                             source="static", message="No module named 'jwt'",
                             file_path="app/utils/auth.py")]
        cache.store(diags, {"app/utils/auth.py": "import jwt"}, idea="a")
        cache.store(diags, {"app/utils/auth.py": "import jwt"}, idea="b")
        old = fdb._FUZZY_MATCH_ENABLED
        fdb._FUZZY_MATCH_ENABLED = False
        try:
            assert cache.lookup_fuzzy(diags) is None
        finally:
            fdb._FUZZY_MATCH_ENABLED = old
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python tests/reliability/test_exp133_fuzzy_fix_cache.py`
Expected: `ERROR ... AttributeError: 'FixCache' object has no attribute 'lookup_fuzzy'`

- [ ] **Step 3: Implement `lookup_fuzzy`**

In `backend/app/knowledge/failure_db.py`, add near the top (with the other module constants, after the imports):

```python
_FUZZY_MATCH_ENABLED     = True   # master switch: compute/observe fuzzy candidates at all
_FUZZY_REPLAY_ENABLED    = False  # shadow mode until a human flips this after reviewing live telemetry
_FUZZY_MIN_SUCCESS_COUNT = 2      # a fuzzy candidate must already be proven under exact matching
```

Add to the `FixCache` class, after `lookup()`:

```python
    def lookup_fuzzy(self, diagnostics: list) -> Optional[CachedFix]:
        """
        Second-tier lookup, meant to be consulted only after an exact
        lookup() miss. Eligibility (Task 5's _is_fuzzy_eligible) is checked
        by the caller in Task 7's orchestrator wiring for now returns based
        purely on the composite-hash index and success_count; Task 5 tightens
        this method itself to fail closed on ineligible diagnostics.
        """
        if not _FUZZY_MATCH_ENABLED:
            return None
        if not diagnostics or not _is_cacheable(diagnostics):
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python tests/reliability/test_exp133_fuzzy_fix_cache.py`
Expected: all tests `PASS`, `8/8 passed`

- [ ] **Step 5: Commit**

```bash
git add backend/app/knowledge/failure_db.py backend/tests/reliability/test_exp133_fuzzy_fix_cache.py
git commit -m "Exp133 (4/8): lookup_fuzzy() behind _FUZZY_MATCH_ENABLED, not yet eligibility-gated"
```

---

### Task 5: Eligibility gates — scaffold allowlist, fail-closed

**Files:**
- Modify: `backend/app/knowledge/failure_db.py` (add `_SCAFFOLD_ALLOWLIST`, `_normalized_path`, `_is_fuzzy_eligible`; call it from `lookup_fuzzy`)
- Test: `backend/tests/reliability/test_exp133_fuzzy_fix_cache.py`

**Interfaces:**
- Consumes: `_diag_field` (Task 3)
- Produces: `_is_fuzzy_eligible(diagnostics: list) -> bool`, now enforced inside `lookup_fuzzy()`.

- [ ] **Step 1: Write the failing tests**

Add to the test file:

```python
def test_fuzzy_eligible_allowlisted_file():
    d = [Diagnostic(error_id="1", category=ErrorCategory.RUNTIME, severity=ErrorSeverity.HIGH,
                     source="static", message="m", file_path="app/routes/auth_routes.py")]
    assert fdb._is_fuzzy_eligible(d) is True


def test_fuzzy_ineligible_non_allowlisted_file():
    d = [Diagnostic(error_id="1", category=ErrorCategory.RUNTIME, severity=ErrorSeverity.HIGH,
                     source="static", message="m", file_path="app/routes/donors_routes.py")]
    assert fdb._is_fuzzy_eligible(d) is False


def test_fuzzy_eligible_import_cascade_no_file():
    d = [Diagnostic(error_id="1", category=ErrorCategory.IMPORT, severity=ErrorSeverity.CRITICAL,
                     source="static", message="No module named 'jwt'")]
    assert fdb._is_fuzzy_eligible(d) is True


def test_fuzzy_ineligible_mixed_group_fails_closed():
    d = [
        Diagnostic(error_id="1", category=ErrorCategory.RUNTIME, severity=ErrorSeverity.HIGH,
                   source="static", message="m", file_path="app/routes/auth_routes.py"),
        Diagnostic(error_id="2", category=ErrorCategory.RUNTIME, severity=ErrorSeverity.HIGH,
                   source="static", message="m2", file_path="app/routes/donors_routes.py"),
    ]
    assert fdb._is_fuzzy_eligible(d) is False


def test_lookup_fuzzy_ignores_ineligible_match():
    with tempfile.TemporaryDirectory() as td:
        _isolated_cache(td)
        cache = fdb.FixCache()
        diags = [Diagnostic(error_id="1", category=ErrorCategory.RUNTIME, severity=ErrorSeverity.HIGH,
                             source="static", message="Some app-specific business logic bug",
                             file_path="app/routes/donors_routes.py")]
        cache.store(diags, {"app/routes/donors_routes.py": "fixed"}, idea="a")
        cache.store(diags, {"app/routes/donors_routes.py": "fixed"}, idea="b")
        # success_count is 2, but donors_routes.py is not in the scaffold allowlist
        assert cache.lookup_fuzzy(diags) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python tests/reliability/test_exp133_fuzzy_fix_cache.py`
Expected: `ERROR ... AttributeError: module 'app.knowledge.failure_db' has no attribute '_is_fuzzy_eligible'`, and `test_lookup_fuzzy_ignores_ineligible_match` fails with a returned hit instead of `None`.

- [ ] **Step 3: Implement the eligibility gate**

In `backend/app/knowledge/failure_db.py`, add below the `_FUZZY_*` constants:

```python
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
```

Then update `lookup_fuzzy()`'s guard clause (the line `if not diagnostics or not _is_cacheable(diagnostics): return None`) to:

```python
        if not _is_fuzzy_eligible(diagnostics):
            return None
```

(replacing the old `_is_cacheable`-only check, since `_is_fuzzy_eligible` already calls `_is_cacheable` internally.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python tests/reliability/test_exp133_fuzzy_fix_cache.py`
Expected: all tests `PASS`, `13/13 passed`

- [ ] **Step 5: Commit**

```bash
git add backend/app/knowledge/failure_db.py backend/tests/reliability/test_exp133_fuzzy_fix_cache.py
git commit -m "Exp133 (5/8): scaffold-file eligibility gate, fail-closed on mixed groups"
```

---

### Task 6: Import/symbol diff metadata (not consumed, seeds future work)

**Files:**
- Modify: `backend/app/knowledge/failure_db.py` (add `_diff_imports` and helpers; update `store()` signature to accept `pre_fix_content`)
- Modify: `backend/app/repair/orchestrator.py` (add `_ProjectSnapshot.get_text()`)
- Test: `backend/tests/reliability/test_exp133_fuzzy_fix_cache.py`

**Interfaces:**
- Consumes: nothing new
- Produces: `FixCache.store(diagnostics, fix_content, idea="", pre_fix_content=None)` (new optional 4th param); `_ProjectSnapshot.get_text(rel_path: str) -> Optional[str]`, used by Task 7's orchestrator wiring.

- [ ] **Step 1: Write the failing tests**

Add to the test file:

```python
def test_diff_python_imports_best_effort():
    old = "from fastapi import FastAPI\n"
    new = "from fastapi import FastAPI\nfrom fastapi.security import OAuth2PasswordBearer, Depends\n"
    mods, syms = fdb._diff_python_imports(old, new)
    assert mods == ["fastapi.security"]
    assert syms == ["Depends", "OAuth2PasswordBearer"]


def test_diff_imports_invalid_python_yields_empty_lists():
    mods, syms = fdb._diff_imports("not valid python (((", "also not valid )))", "app/utils/auth.py")
    assert mods == [] and syms == []


def test_store_populates_import_metadata_from_pre_fix_content():
    with tempfile.TemporaryDirectory() as td:
        _isolated_cache(td)
        cache = fdb.FixCache()
        diags = [Diagnostic(error_id="1", category=ErrorCategory.IMPORT, severity=ErrorSeverity.CRITICAL,
                             source="static", message="No module named 'jwt'",
                             file_path="app/utils/auth.py")]
        new_content = "from fastapi.security import OAuth2PasswordBearer\n"
        h = cache.store(diags, {"app/utils/auth.py": new_content}, idea="app",
                         pre_fix_content={"app/utils/auth.py": ""})
        entry = cache._data[h]
        assert "fastapi.security" in entry["imports_added"]
        assert "OAuth2PasswordBearer" in entry["symbols_added"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python tests/reliability/test_exp133_fuzzy_fix_cache.py`
Expected: `ERROR ... AttributeError: module 'app.knowledge.failure_db' has no attribute '_diff_python_imports'`

- [ ] **Step 3: Implement the diff helpers and wire into `store()`**

In `backend/app/knowledge/failure_db.py`, add `import ast` to the imports, then add these functions (placed after `_composite_hash`, before the `FixCache` class):

```python
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
```

Now update `FixCache.store()`'s signature and body (from Task 3) to accept and use `pre_fix_content`:

```python
    def store(self, diagnostics: list, fix_content: dict, idea: str = "",
              pre_fix_content: Optional[dict] = None) -> str:
```

Inside the method, right before `ts = time.strftime(...)`, add:

```python
        imports_added, symbols_added = set(), set()
        if pre_fix_content:
            for rel_path, new_content in fix_content.items():
                old_content = pre_fix_content.get(rel_path)
                if old_content is None:
                    continue
                added_mods, added_syms = _diff_imports(old_content, new_content, rel_path)
                imports_added.update(added_mods)
                symbols_added.update(added_syms)
```

And replace both `"imports_added": []` / `"symbols_added": []` occurrences (in the `existing` update branch add these two keys, and in the new-entry branch replace the placeholders) with:

```python
                "imports_added": sorted(imports_added),
                "symbols_added": sorted(symbols_added),
```

(For the `existing` branch, which didn't previously set these keys at all, add `existing["imports_added"] = sorted(imports_added)` and `existing["symbols_added"] = sorted(symbols_added)` alongside its other field updates.)

Finally, in `backend/app/repair/orchestrator.py`, add a text accessor to `_ProjectSnapshot` (after its existing `revert()` method):

```python
    def get_text(self, rel_path: str) -> Optional[str]:
        """Decoded pre-fix content for one file, or None if it wasn't
        captured (e.g. a newly-created file that didn't exist pre-fix)."""
        data = self._snap.get(rel_path)
        if data is None:
            return None
        try:
            return data.decode("utf-8", errors="replace")
        except Exception:
            return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python tests/reliability/test_exp133_fuzzy_fix_cache.py`
Expected: all tests `PASS`, `16/16 passed`

- [ ] **Step 5: Commit**

```bash
git add backend/app/knowledge/failure_db.py backend/app/repair/orchestrator.py backend/tests/reliability/test_exp133_fuzzy_fix_cache.py
git commit -m "Exp133 (6/8): best-effort import/symbol diff metadata, not yet consumed"
```

---

### Task 7: Shadow-mode wiring into the orchestrator

**Files:**
- Modify: `backend/app/core/context.py` (add `self.fuzzy_cache_group_ids` to `GenerationContext.__init__`, near `self.prevention_counts`)
- Modify: `backend/app/repair/orchestrator.py`:
  - Replace the inline "Fix cache lookup" block in `_apply_fix_group` (current lines ~695-716) with a call to a new `_try_cache_fix()` helper
  - Add `_apply_cached_fix()` and `_try_cache_fix()` helper functions
  - `run_attempt()`: reset `ctx.fuzzy_cache_group_ids = set()` alongside `group_fix_contents = []` (line ~1163)
  - The commit loop (lines ~1315-1327): attribute `fix_cache_fuzzy_success`/`fix_cache_fuzzy_failed`, and pass `pre_fix_content` into `fix_cache.store()`
- Test: `backend/tests/reliability/test_exp133_fuzzy_fix_cache.py`

**Interfaces:**
- Consumes: `failure_db.fix_cache.lookup()` / `.lookup_fuzzy()` / `_FUZZY_REPLAY_ENABLED` (Tasks 3-5), `_ProjectSnapshot.get_text()` (Task 6)
- Produces: `_try_cache_fix(group, ctx) -> Optional[tuple[list[str], dict[str,str]]]` — `None` means "no cache hit, proceed to the LLM exactly as before this experiment."

- [ ] **Step 1: Write the failing tests**

Add to the test file (needs `DiagnosticGroup`, `FixStrategy` imports and access to the orchestrator module):

```python
import app.repair.orchestrator as orch
from app.core.context import DiagnosticGroup, FixStrategy


class _FakeCtx:
    """Minimal stand-in -- _try_cache_fix/_apply_cached_fix only touch
    project_path, prevention_counts, and fuzzy_cache_group_ids."""
    def __init__(self, project_path):
        self.project_path = project_path
        self.prevention_counts = {}
        self.fuzzy_cache_group_ids = set()


def _make_group(diags):
    return DiagnosticGroup(group_id="g1", root_cause="test", diagnostics=diags,
                            affected_files=[d.file_path for d in diags if d.file_path],
                            suggested_strategy=FixStrategy.PATCH_FILE, priority=1)


def test_shadow_mode_logs_but_does_not_apply():
    with tempfile.TemporaryDirectory() as td:
        _isolated_cache(td)
        cache = fdb.FixCache()
        diags = [Diagnostic(error_id="1", category=ErrorCategory.IMPORT, severity=ErrorSeverity.CRITICAL,
                             source="static", message="No module named 'jwt'",
                             file_path="app/utils/auth.py")]
        cache.store(diags, {"app/utils/auth.py": "import jwt  # cached fix"}, idea="a")
        cache.store(diags, {"app/utils/auth.py": "import jwt  # cached fix"}, idea="b")

        project_dir = Path(td) / "project"
        (project_dir / "app" / "utils").mkdir(parents=True)
        target = project_dir / "app" / "utils" / "auth.py"
        target.write_text("import jwt  # original\n", encoding="utf-8")

        ctx = _FakeCtx(project_dir)
        group = _make_group(diags)

        old_flag = fdb._FUZZY_REPLAY_ENABLED
        fdb._FUZZY_REPLAY_ENABLED = False
        try:
            result = orch._try_cache_fix(group, ctx)
        finally:
            fdb._FUZZY_REPLAY_ENABLED = old_flag

        assert result is None, "shadow mode must fall through to the LLM path"
        assert ctx.prevention_counts.get("fix_cache_fuzzy_shadow_hit") == 1
        assert target.read_text(encoding="utf-8") == "import jwt  # original\n", \
            "shadow mode must never write the fuzzy-matched content to disk"


def test_replay_enabled_applies_fuzzy_fix():
    with tempfile.TemporaryDirectory() as td:
        _isolated_cache(td)
        cache = fdb.FixCache()
        diags = [Diagnostic(error_id="1", category=ErrorCategory.IMPORT, severity=ErrorSeverity.CRITICAL,
                             source="static", message="No module named 'jwt'",
                             file_path="app/utils/auth.py")]
        cache.store(diags, {"app/utils/auth.py": "import jwt  # cached fix"}, idea="a")
        cache.store(diags, {"app/utils/auth.py": "import jwt  # cached fix"}, idea="b")

        project_dir = Path(td) / "project"
        (project_dir / "app" / "utils").mkdir(parents=True)
        target = project_dir / "app" / "utils" / "auth.py"
        target.write_text("import jwt  # original\n", encoding="utf-8")

        ctx = _FakeCtx(project_dir)
        group = _make_group(diags)

        old_flag = fdb._FUZZY_REPLAY_ENABLED
        fdb._FUZZY_REPLAY_ENABLED = True
        try:
            result = orch._try_cache_fix(group, ctx)
        finally:
            fdb._FUZZY_REPLAY_ENABLED = old_flag

        assert result is not None
        modified, fix_content = result
        assert "app/utils/auth.py" in modified
        assert target.read_text(encoding="utf-8") == "import jwt  # cached fix"
        assert "g1" in ctx.fuzzy_cache_group_ids
        assert ctx.prevention_counts.get("fix_cache_fuzzy_hit") == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python tests/reliability/test_exp133_fuzzy_fix_cache.py`
Expected: `ERROR ... AttributeError: module 'app.repair.orchestrator' has no attribute '_try_cache_fix'`

- [ ] **Step 3: Add `fuzzy_cache_group_ids` to `GenerationContext`**

In `backend/app/core/context.py`, find `self.prevention_counts: dict = {}` (line 317) and add directly after it:

```python
        # Exp133: which DiagnosticGroup ids in the CURRENT fix attempt were
        # resolved via a fuzzy (not exact) cache hit -- reset at the top of
        # every FixOrchestrator.run_attempt() call. Used only to attribute
        # fix_cache_fuzzy_success/_failed telemetry once verification
        # confirms whether that group's errors actually cleared.
        self.fuzzy_cache_group_ids: set = set()
```

- [ ] **Step 4: Add `_apply_cached_fix` and `_try_cache_fix`, rewire `_apply_fix_group`**

In `backend/app/repair/orchestrator.py`, replace the existing "Fix cache lookup" block inside `_apply_fix_group` (the `try: from app.knowledge.failure_db import fix_cache ... except Exception: pass` block, roughly lines 695-716) with:

```python
    cached_result = _try_cache_fix(group, ctx)
    if cached_result is not None:
        return cached_result
```

Then add these two new module-level functions directly above `_apply_fix_group`:

```python
def _apply_cached_fix(cached, ctx: GenerationContext, tag: str) -> list[str]:
    """Write a CachedFix's content to disk. Shared by the exact and fuzzy
    tiers so both print/behave identically except for the tag."""
    modified: list[str] = []
    for rel_path, content in cached.fix_content.items():
        target = _safe_patch_target(ctx.project_path, rel_path)
        if target is None:
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            modified.append(rel_path)
            print(f"    [fix] ({tag} cache) Patched: {rel_path}")
        except Exception as exc:
            print(f"    [fix] ({tag} cache) Write failed for {rel_path}: {exc}")
    return modified


def _try_cache_fix(group: DiagnosticGroup, ctx: GenerationContext):
    """
    Consults the fix cache (exact, then fuzzy) before any LLM call.
    Returns (modified_paths, fix_content) if a cached fix was applied,
    or None if the caller should proceed to the normal LLM path.

    Exp133 shadow mode: a fuzzy hit is only ever applied when
    app.knowledge.failure_db._FUZZY_REPLAY_ENABLED is True. While False, a
    fuzzy hit is logged (prevention_counts["fix_cache_fuzzy_shadow_hit"])
    and this returns None, so the caller falls through to the LLM exactly
    as it did before this experiment existed.
    """
    try:
        from app.knowledge import failure_db
    except Exception:
        return None

    try:
        cache_hit = failure_db.fix_cache.lookup(group.diagnostics)
        if cache_hit:
            print(f"    [fix] Cache HIT (exact) for group {group.group_id} "
                  f"(seen {cache_hit.success_count}x before) — skipping LLM")
            modified = _apply_cached_fix(cache_hit, ctx, tag="exact")
            return modified, dict(cache_hit.fix_content)
    except Exception:
        pass  # exact cache unavailable — fall through

    try:
        fuzzy_hit = failure_db.fix_cache.lookup_fuzzy(group.diagnostics)
    except Exception:
        fuzzy_hit = None

    if not fuzzy_hit:
        return None

    if failure_db._FUZZY_REPLAY_ENABLED:
        print(f"    [fix] Cache HIT (fuzzy) for group {group.group_id} "
              f"(seen {fuzzy_hit.success_count}x before) — skipping LLM")
        modified = _apply_cached_fix(fuzzy_hit, ctx, tag="fuzzy")
        ctx.fuzzy_cache_group_ids.add(group.group_id)
        ctx.prevention_counts["fix_cache_fuzzy_hit"] = (
            ctx.prevention_counts.get("fix_cache_fuzzy_hit", 0) + 1)
        return modified, dict(fuzzy_hit.fix_content)

    print(f"    [fix] Cache SHADOW-HIT (fuzzy, not applied) for group "
          f"{group.group_id} (seen {fuzzy_hit.success_count}x before)")
    ctx.prevention_counts["fix_cache_fuzzy_shadow_hit"] = (
        ctx.prevention_counts.get("fix_cache_fuzzy_shadow_hit", 0) + 1)
    return None
```

- [ ] **Step 5: Run the new tests to verify they pass**

Run: `cd backend && python tests/reliability/test_exp133_fuzzy_fix_cache.py`
Expected: all tests `PASS`, `18/18 passed`

- [ ] **Step 6: Wire `run_attempt()`'s reset and the commit-loop telemetry/pre_fix_content**

In `backend/app/repair/orchestrator.py`'s `run_attempt()`, find:
```python
        group_fix_contents: list[tuple[DiagnosticGroup, dict[str, str]]] = []
```
and add directly after it:
```python
        ctx.fuzzy_cache_group_ids = set()
```

Then find the commit loop:
```python
        if success and group_fix_contents:
            try:
                from app.knowledge.failure_db import fix_cache
                post_fix_ids = {d.error_id for d in ctx.all_diagnostics()}
                for g, fix_content in group_fix_contents:
                    group_ids = {d.error_id for d in g.diagnostics}
                    if group_ids & post_fix_ids:
                        # At least one of this group's errors is still present —
                        # this patch did NOT resolve the group; don't cache it.
                        continue
                    fix_cache.store(g.diagnostics, fix_content, idea=getattr(ctx, "idea", ""))
            except Exception as exc:
                print(f"[fix] fix_cache.store failed (fix worked but won't be reused): {exc}")
```
and replace it with:
```python
        if success and group_fix_contents:
            try:
                from app.knowledge.failure_db import fix_cache
                post_fix_ids = {d.error_id for d in ctx.all_diagnostics()}
                for g, fix_content in group_fix_contents:
                    group_ids = {d.error_id for d in g.diagnostics}
                    cleared = not (group_ids & post_fix_ids)
                    if g.group_id in ctx.fuzzy_cache_group_ids:
                        key = "fix_cache_fuzzy_success" if cleared else "fix_cache_fuzzy_failed"
                        ctx.prevention_counts[key] = ctx.prevention_counts.get(key, 0) + 1
                    if not cleared:
                        # At least one of this group's errors is still present —
                        # this patch did NOT resolve the group; don't cache it.
                        continue
                    pre_fix_content = {p: snapshot.get_text(p) for p in fix_content}
                    fix_cache.store(g.diagnostics, fix_content, idea=getattr(ctx, "idea", ""),
                                     pre_fix_content=pre_fix_content)
            except Exception as exc:
                print(f"[fix] fix_cache.store failed (fix worked but won't be reused): {exc}")
```

- [ ] **Step 7: Run the full test file once more**

Run: `cd backend && python tests/reliability/test_exp133_fuzzy_fix_cache.py`
Expected: `18/18 passed` (this step's changes aren't directly unit-tested in isolation — `run_attempt()` requires a full `GenerationContext` + verification engine — but Task 8's canary run exercises this code path end-to-end)

- [ ] **Step 8: Commit**

```bash
git add backend/app/core/context.py backend/app/repair/orchestrator.py backend/tests/reliability/test_exp133_fuzzy_fix_cache.py
git commit -m "Exp133 (7/8): wire shadow-mode fuzzy lookup into the repair loop"
```

---

### Task 8: Offline validation — full suite + 3-app canary

**Files:** none (validation only)

- [ ] **Step 1: Run the full reliability test suite**

Run: `cd backend && python -m pytest tests/reliability/ -q`
Expected: all tests pass, including the new `test_exp133_fuzzy_fix_cache.py` (pytest will also collect and run its plain `test_*` functions) and every pre-existing test file (confirms nothing in `context.py`/`orchestrator.py`/`failure_db.py` regressed).

- [ ] **Step 2: Run the fixed 3-app canary**

Run: `cd backend && python scripts/run_canary.py --no-deploy`
Expected: completes without crashing; scores for todo/blog_cms/crm are within normal run-to-run variance of the previous entry in `backend/benchmark_results/canary_history.json` (this experiment is not expected to change these 3 apps' outcomes — the fuzzy tier is unlikely to fire at all within one run of the same fixed apps, since they already hit the exact-match cache; this run is a regression check, not a demonstration of fuzzy uplift, per the frozen spec's validation plan).

- [ ] **Step 3: Confirm shadow telemetry is wired, not just present in code**

After the canary run, inspect the tail of `backend/failure_memory/generation_log.jsonl`:
```bash
python -c "import json; print(json.loads(open('failure_memory/generation_log.jsonl', encoding='utf-8').read().splitlines()[-1])['prevention_counts'])"
```
Expected: a valid `prevention_counts` dict (may or may not contain `fix_cache_fuzzy_shadow_hit` this run — absence is fine and expected for a 3-app run; the check here is that the run completed and logged normally, not that a shadow hit occurred).

- [ ] **Step 4: Log the result in experiments.md and commit**

Append a new `## Experiment 133: ...` entry to `experiments.md` (repo root) summarizing: the shadow-mode implementation, the proxy-analysis numbers from the frozen spec, canary result (pass/fail and score deltas), and that `_FUZZY_REPLAY_ENABLED` remains `False` pending live shadow-telemetry review per the spec's kill/success criteria.

```bash
git add experiments.md
git commit -m "Exp133 (8/8): log shadow-mode validation result"
```

---

## Acceptance Checklist

| Check | Pass criterion | Verified by |
|---|---|---|
| Exact cache behavior | 100% identical when fuzzy matching is disabled | Task 4 (`lookup_fuzzy` is a new, separate method; `lookup()` is untouched); Task 8's canary run |
| Backward compatibility | Existing 252 cache entries load without migration | Task 1's `test_old_entries_load_without_migration` |
| Performance | Cache lookup overhead negligible | Task 3's in-memory index (dict lookup, no I/O per call); not micro-benchmarked separately since the index is O(1) dict access over ≤252 entries |
| Precision | Fuzzy replay precision ≥ 95% | Not measurable within this plan — `fix_cache_fuzzy_success`/`_failed` (Task 7) accrue only once `_FUZZY_REPLAY_ENABLED=True`, which this plan explicitly does not flip. Prospective, per the frozen spec. |
| Safety | No canary regressions attributable to fuzzy replay | Task 8's canary run (regression check on the always-active exact-match path; fuzzy is shadow-only, so it cannot cause a canary regression by construction) |
| Cost | Measurable reduction in LLM repair calls | Prospective — requires the larger benchmark run and the flag flip, both out of scope here |
| Quality | No ForgeScore decrease vs. exact-cache baseline | Task 8's canary comparison against `canary_history.json`'s previous entry |

Precision/Cost/Quality-at-scale are intentionally left as prospective/out-of-scope for this plan, matching the frozen spec's kill criteria: those numbers can only be produced by running shadow mode live for 200-300 apps, which is a data-collection period, not an implementation task.
