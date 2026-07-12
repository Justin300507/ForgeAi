# Write Pipeline (Experiments 066-067)

2026-07-12. Covers the file-write layer only — everything downstream of
"the LLM/patcher produced a `{path, content}` pair," not generation or
repair decision-making itself.

## Scope note: five write call sites exist across three write functions

Exp066 identified three independent write *mechanisms*. Exp067's own
Part 1 audit corrected that count at the call-site level: `_safe_patch_target()`
+ direct `target.write_text()` is not unique to `_regenerate_module()` —
the identical pattern is used at **five call sites total** inside
`app/repair/orchestrator.py`: four inside `_apply_fix_group()`
(missing-import synthesis ~line 385/404, the seed-stub write ~line
612/615, the fix-cache replay ~line 630/635, and one more ~line
705/764) and one inside `_regenerate_module()` (~line 821/831). Exp067's
mission explicitly named only `_regenerate_module()`, and its rules
forbade "refactoring outside this write path" — so **only
`_regenerate_module()`'s own write call was hardened**; the other four
`_apply_fix_group()` call sites still use the fully-original,
unhardened pattern. This is flagged here as a corrected, evidence-based
finding, not a silently-expanded or silently-missed scope.

1. `app/services/file_writer_service.py::write_files()` — the bulk
   initial-generation writer. **Hardened in Exp066.**
2. `app/services/fix_writer_service.py::write_fix()` — the single-file
   repair-time writer. **Hardened in Exp066.**
3. `app/repair/orchestrator.py::_regenerate_module()` (lines 777-855) —
   one repair strategy's inline write path
   (`regenerate_arch`/module-regen backend branch). Uses
   `_safe_patch_target()` (`app/repair/orchestrator.py:30-51`,
   unchanged) for path validation, **now with an added
   `has_windows_drive_or_unc()` defense-in-depth check scoped to this
   function only** (Exp067), and now writes via `atomic_write_text()`
   instead of `target.write_text()` directly (Exp067). **Hardened in
   Exp067**, see `docs/WRITE_VALIDATION_MATRIX.md` for exactly what
   changed and why `_safe_patch_target()` itself was not touched.
4. `app/repair/orchestrator.py::_apply_fix_group()` (4 call sites:
   ~385-404, ~612-615, ~630-635, ~705-764) — same
   `_safe_patch_target()` + direct `target.write_text()` pattern as
   `_regenerate_module()` had before Exp067, but **not named in either
   Exp066's or Exp067's mission, and therefore not touched by either
   experiment.** No atomic write, no drive/UNC defense-in-depth check.
   **Confirmed residual asymmetry, flagged for a future experiment.**

Also found, and deliberately left alone as **not LLM-controlled input**
(no path-traversal surface exists because the paths are fixed
constants, not attacker/model-controlled strings):

- `app/services/deployment_config_service.py::generate_deployment_configs()`
  (lines 204-231) — writes `render.yaml`, `Procfile`, `.env.example`,
  a GitHub Actions workflow, and `wrangler.toml` from static internal
  keys via `Path.write_text()` directly (non-atomic, no rollback).
  Low severity: these are deterministic deploy configs, not app code,
  and don't affect the CRUD journey or Forge Score. Not touched.
- The frontend/PWA template-scaffold loop inside `write_files()`
  itself (`file_writer_service.py:674-679`) and the `app/database.py`
  fallback block (`:629-645`) — both loop over static template dicts,
  not LLM output. These WERE brought onto the new `atomic_write_text`
  helper (crash-safety is free and correct there), but they never
  needed the path-traversal check, since there is nothing
  attacker-controlled to validate.

## Entry points → callers → write function

```
POST /project, /project/v15, /jobs, ...
  -> app/services/v6_orchestrator.py: generate_project_v6()
       -> write_files(project_name, all_files, ...)      [v6_orchestrator.py:289]
            (ONE call per generation run — bulk write of the entire project)

Verification/repair loop (same generate_project_v6() call, after initial write)
  -> app/repair/orchestrator.py: repair_project() / strategies
       -> _apply_fix_group() / _patch_single_file(), etc.
            -> write_fix(project_path, fix)                [v6_orchestrator.py: ~11 call sites
                                                              across the validation loop and the
                                                              runtime-fix loop, e.g. lines 534,
                                                              548, 560, 663, 748, 836, 1130,
                                                              1139, 1190, 1216]
            -> app/services/runtime_fix_writer.py            [1 call site, runtime-fix delegate]
       -> _regenerate_module() [orchestrator.py:777-855]   (HARDENED Exp067)
            -> has_windows_drive_or_unc() check, then _safe_patch_target(),
               then atomic_write_text()   [orchestrator.py: ~1 write call site]
       -> _apply_fix_group() [orchestrator.py:588-777]     (OUT OF SCOPE, not named by either
                                                              Exp066 or Exp067's mission)
            -> target.write_text(...) directly, guarded by _safe_patch_target()
               [orchestrator.py: 4 write call sites, ~404/615/635/764]
       -> _regenerate_architecture() [orchestrator.py:857+]
            -> app/services/v6_orchestrator.py: generate_project_v6()  (recurses back to write_files())
```

`write_files()` is called exactly once per generation (the bulk
initial write, wiping and recreating the whole project directory).
`write_fix()` is called many times per repair loop (one call per
fixed file). Both ultimately funnel through the same two new shared
utilities added this cycle:

```
app/utils/safe_path.py        resolve_safe_path() / is_safe_path() / PathTraversalError
app/utils/atomic_write.py     atomic_write_text()
```

## Write lifecycle (per file, both `write_files()` and `write_fix()`)

```
1. path safety     resolve_safe_path(base_dir, path)   -- reject/skip on PathTraversalError
2. newline/create   _normalize_newlines(), _ensure_create_all()   (content-shape fixups, pre-existing)
3. syntax           _is_safe_to_write(path, content)    -- ast.parse(); auto-repair attempt; reject/skip on failure
4. semantic         _check_request_field_consistency()  -- reject/skip if a Pydantic request param's
                                                            attribute access doesn't match a declared field
5. mkdir parents     os.makedirs(full_path.parent, exist_ok=True)
6. atomic write      atomic_write_text(full_path, content)  -- temp file in same dir, fsync, os.replace()
```

Step order matters: path safety runs **first**, before any content
processing — a malicious/hallucinated path must never reach even the
newline-normalization step, let alone the filesystem. Steps 2-4 are
content checks with no path risk left to guard against by that point.

### `write_files()`-specific lifecycle notes

- `write_files()` wipes and recreates the entire project directory
  before the loop (`shutil.rmtree` + `os.makedirs`, with Windows
  read-only-retry logic, `file_writer_service.py:525-550`) — this is
  the "atomicity" boundary for a *whole generation run*, and pre-dates
  this experiment; not altered here (rules: behavior-preserving only).
- A per-file failure (bad path, syntax, semantic) is a `continue` —
  the loop keeps going, the rest of the batch still gets written.
  There is no whole-batch rollback; a partially-invalid LLM response
  produces a partially-populated project, same as before this
  experiment (Exp065's `docs/RELIABILITY_REVIEW.md` already documents
  that this is the repair loop's job to backfill, not the writer's).
- New counters/timing added (`file_writer_service.py:554-560,
  624-627`): one summary line per generation run —
  `wrote N files in Xms (skipped: A unsafe path, B syntax, C semantic)`
  — closing the "logging/timing/metrics" gap `docs/ARCHITECTURE_REVIEW.md`
  and `docs/TEST_QUALITY_REVIEW.md` flagged for this function.

### `write_fix()`-specific lifecycle notes

- Single-file, single-call — no batch/loop semantics.
- The `app/database.py` special case (`fix_writer_service.py:326-330`)
  short-circuits to `patch_database_py()` and returns before any of
  the write steps above run — unchanged this cycle.
- The flat-file/package-conflict cleanup (`:354-357`, removing a
  conflicting `app/utils.py` when writing `app/utils/auth.py`) and the
  `__init__.py`-creation step (`:365-371`) both run between path
  safety and the final atomic write — unchanged in position, the
  `__init__.py` creation itself now goes through `atomic_write_text`
  instead of `open(...).close()`.

### `_regenerate_module()`-specific lifecycle notes (Exp067)

`_regenerate_module()`'s backend branch has a **different** lifecycle
shape from `write_files()`/`write_fix()` — it does not share
`resolve_safe_path()`, because `_safe_patch_target()`'s semantics are
genuinely different (see below) and this experiment's rules forbade
touching the shared function (it has 4 other call sites, all out of
scope):

```
1. presence check    if not rel or not content: continue
2. drive/UNC check    has_windows_drive_or_unc(rel)        -- Exp067, scoped to this
                                                                loop only, NOT inside
                                                                _safe_patch_target()
3. path safety        _safe_patch_target(project_path, rel) -- pre-existing, unchanged;
                                                                DOES allow an absolute
                                                                path if it resolves inside
                                                                the project root (see below)
4. syntax (py only)   _python_syntax_error(content)          -- pre-existing, unchanged
5. mkdir parents       target.parent.mkdir(parents=True, exist_ok=True)
6. atomic write        atomic_write_text(target, content)   -- Exp067 (was target.write_text())
```

**Why `_safe_patch_target()` allows absolute paths and `resolve_safe_path()`
doesn't, and why that's not a bug to fix:** `_safe_patch_target()`'s own
docstring (line 34-36) documents a real, observed LLM behavior — fix
prompts show diagnostics with absolute file paths, and the model often
echoes them back verbatim (e.g. `/app/generated_projects/x/src/App.jsx`).
Rejecting all absolute paths outright (what `resolve_safe_path()` does)
would reject legitimate fixes this call site actually receives. This is
why Exp067's Part 2 concluded a full swap to `resolve_safe_path()` is
**unsafe** (see `docs/WRITE_VALIDATION_MATRIX.md`), and instead added
the drive/UNC check as a narrow, additive, non-behavior-changing
supplement.

**Why the drive/UNC check runs BEFORE `_safe_patch_target()`, not
inside it:** `_safe_patch_target()` is shared by 4 other call sites in
this file this experiment's "no refactoring outside this write path"
rule put out of scope. Adding the check as a separate, early guard
inside `_regenerate_module()`'s own loop closes the gap for this write
path specifically without touching the shared function's behavior for
the other 4 sites.

**What the drive/UNC check actually closes:** empirical testing (not
assumption) found `_safe_patch_target()` on its own already correctly
blocks Windows-drive-**absolute** paths (`C:\evil.py`) and UNC paths
(`\\server\share\evil.py`) on this host — `os.path.isabs()` recognizes
both shapes on Windows, so the existing containment check catches them.
The one shape it does **not** catch is a drive-**relative** path
(`C:evil.py` — no backslash): `os.path.isabs()` returns `False` for
this shape, so it falls into the relative branch and silently lands
inside the project root under a prefix-stripped name. This was never a
sandbox *escape* (the file always stayed contained), just an
unintended-interpretation gap for a path shape no legitimate fix would
ever emit — closed as defense-in-depth, not as an escape fix.

**Exception handling (unchanged, but now closer to the write):** the
entire backend branch is wrapped in a single
`try: ... except Exception as exc: print(...); modified, fix_content_map = _apply_fix_group(...)`.
This means an exception raised by `atomic_write_text()` (e.g. a genuine
disk-full or permission failure) now routes to the **same pre-existing
fallback** any other exception in this block always did — falling back
to `_apply_fix_group()`, a *different*, unhardened write path (see the
scope note above). This is unchanged behavior, not something Exp067
introduced, but is worth knowing: a failed atomic write in
`_regenerate_module()` does not simply fail the fix, it silently
reroutes to a sibling write mechanism with weaker guarantees. Flagged,
not fixed (fixing it would mean changing the except-fallback, which is
repair-logic control flow, out of scope for a write-pipeline-only
experiment).

## Failure handling

Every guard in the lifecycle above is a **skip-and-continue** (for
`write_files()`) or **return False** (for `write_fix()`), never an
unhandled exception reaching the caller — this was already true before
this experiment for the syntax/semantic checks; the new path check
follows the identical convention (`except PathTraversalError: skip`,
matching `write_fix`'s pre-existing `blocked suspicious path` print).
A rejected file is expected to be caught downstream by
`validate_project()` and backfilled by the repair loop — this is the
established, pre-existing philosophy for every guard in this function,
not new behavior introduced by this experiment.

`atomic_write_text()` itself raises on genuine I/O failure (disk full,
permission denied) rather than swallowing it — this is a deliberate,
behavior-preserving choice: the previous direct `open(path, "w")` also
raised on I/O failure, so wrapping it does not change what already
propagated to the caller. What changes is that a failure now can never
leave a *partially-written* file at the final path (see
`docs/WRITE_SECURITY.md` for the atomicity argument in detail).

## Diagram

```
                    ┌─────────────────────────────┐
                    │  generate_project_v6()        │
                    └───────────────┬───────────────┘
                                    │  (once, all files)
                                    ▼
                    ┌─────────────────────────────┐
                    │  write_files()                 │  file_writer_service.py
                    │  ┌─────────────────────────┐  │
                    │  │ for each {path, content}│  │
                    │  │  1. resolve_safe_path    │  │  <-- Exp066 (was MISSING)
                    │  │  2. normalize/create_all │  │
                    │  │  3. _is_safe_to_write    │  │  (pre-existing)
                    │  │  4. semantic check       │  │  <-- Exp066 (reused from write_fix)
                    │  │  5. atomic_write_text    │  │  <-- Exp066 (was open(...,"w"))
                    │  └─────────────────────────┘  │
                    └─────────────────────────────┘
                                    │
                                    ▼  (verification/repair loop, N calls)
                    ┌─────────────────────────────┐
                    │  write_fix()                   │  fix_writer_service.py
                    │  ┌─────────────────────────┐  │
                    │  │  1. resolve_safe_path    │  │  <-- Exp066 (was a narrower inline check)
                    │  │  2. database.py special- │  │
                    │  │     case shortcut         │  │
                    │  │  3. normalize/create_all │  │
                    │  │  4. _is_safe_to_write    │  │  (pre-existing)
                    │  │  5. semantic check       │  │  (Exp064, unchanged)
                    │  │  6. flat-conflict cleanup│  │  (pre-existing)
                    │  │  7. __init__.py ensure   │  │  <-- Exp066 (now atomic)
                    │  │  8. atomic_write_text    │  │  <-- Exp066 (was open(...,"w"))
                    │  └─────────────────────────┘  │
                    └─────────────────────────────┘

  Both share:  app/utils/safe_path.py (resolve_safe_path/is_safe_path/PathTraversalError)
               app/utils/atomic_write.py (atomic_write_text)

                                    ▼  (one repair strategy: regenerate_arch/module-regen)
                    ┌─────────────────────────────┐
                    │  _regenerate_module()          │  orchestrator.py (Exp067)
                    │  ┌─────────────────────────┐  │
                    │  │  1. drive/UNC check      │  │  <-- Exp067 (scoped to this function only)
                    │  │  2. _safe_patch_target   │  │  (pre-existing, unchanged -- allows
                    │  │                           │  │      absolute-if-contained, unlike
                    │  │                           │  │      resolve_safe_path)
                    │  │  3. _python_syntax_error │  │  (pre-existing)
                    │  │  4. atomic_write_text    │  │  <-- Exp067 (was target.write_text())
                    │  └─────────────────────────┘  │
                    └─────────────────────────────┘
                       shares app/utils/atomic_write.py and app/utils/safe_path.py's
                       has_windows_drive_or_unc() (NOT resolve_safe_path itself --
                       see docs/WRITE_VALIDATION_MATRIX.md for why not)

  STILL NOT sharing anything (out of scope for both Exp066 and Exp067 --
  not named by either mission):
  app/repair/orchestrator.py::_apply_fix_group() — 4 write call sites, same
  legacy _safe_patch_target() + direct target.write_text() pattern
  _regenerate_module() had before Exp067. No atomic write, no drive/UNC check.
```
