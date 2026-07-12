# Write Validation Symmetry Matrix (Experiment 066)

2026-07-12. Before this experiment, `write_files()` and `write_fix()`
were asymmetric in almost every dimension below (this was Exp065's
Finding #1 and the direct justification for this experiment). After
this experiment they are symmetric in everything that's genuinely
shared infrastructure, and *intentionally* asymmetric only where a
real difference in what each function does justifies it (documented
per-row).

| Dimension | `write_files()` (bulk, initial gen) | `write_fix()` (single-file, repair) | Symmetric now? |
|---|---|---|---|
| **Path-traversal validation** | `resolve_safe_path()` (`file_writer_service.py:573`) | `resolve_safe_path()` (`fix_writer_service.py:329`) | **Yes** — same function, same module, same failure mode (`PathTraversalError` → skip/reject) |
| **Absolute-path rejection** | via `resolve_safe_path()` | via `resolve_safe_path()` | **Yes** |
| **Windows drive / UNC rejection** | via `resolve_safe_path()` | via `resolve_safe_path()` | **Yes** — this is a *new* capability neither function had before this experiment |
| **Symlink-escape rejection** | via `resolve_safe_path()`'s use of `Path.resolve()` | via `resolve_safe_path()`'s use of `Path.resolve()` | **Yes** in code; unverified live in this dev environment for either function (see `docs/WRITE_SECURITY.md` #5) |
| **Syntax (compile) validation** | `_is_safe_to_write()` (`file_writer_service.py:457`) — has auto-repair fallbacks (backslash-syntax fix, indent-error fix, param-order fix) before giving up | `_is_safe_to_write()` (`fix_writer_service.py:88`) — a **separate, simpler implementation**, no auto-repair fallbacks | **No — intentionally not merged.** These are two different functions with the same name in two different files; Part 4 of this experiment explicitly required "Do NOT merge unless behavior remains identical," and they are not identical (`write_files`'s version tries 3 auto-repair strategies before rejecting; `write_fix`'s does not). Merging would either strip `write_files`' auto-repair (a real behavior regression) or add unrequested auto-repair to `write_fix` (a repair-logic change, out of scope). Left as two functions. |
| **Semantic (request/field) validation** | `_check_request_field_consistency()`, reused via a function-local import from `fix_writer_service.py` (`file_writer_service.py:603`) | `_check_request_field_consistency()`, defined here (`fix_writer_service.py:267`) | **Yes — same function, not duplicated.** This closes the exact gap Exp065 flagged: `write_files()` previously had zero semantic validation while `write_fix()` had it from Exp064. |
| **Atomic writes** | `atomic_write_text()` (`file_writer_service.py:621`, plus the `app/database.py` fallback and template-scaffold loop) | `atomic_write_text()` (`fix_writer_service.py:375`, plus the `__init__.py`-creation step) | **Yes** — same helper, same module |
| **Rollback on write failure** | `atomic_write_text()`'s temp-file cleanup + re-raise (no partial file ever visible at the final path) | same | **Yes** |
| **Overwrite behavior** | Overwrites unconditionally (by design — regenerates the whole project) | Overwrites unconditionally (by design — replaces a broken file) | **Yes**, and correctly so — both functions are supposed to overwrite; this was never a gap |
| **Logging on rejection** | `print(f"  [file_writer] blocked suspicious path: ...")` / `"...semantically inconsistent..."` / `_is_safe_to_write`'s own `SKIPPING TRUNCATED/INVALID FILE` dump | `print(f"write_fix: blocked suspicious path: ...")` / `"REFUSING SEMANTICALLY INCONSISTENT WRITE"` / `_is_safe_to_write`'s own rejection print | **Symmetric in spirit** (both log every rejection with the reason), not identical wording — each already had its own established log-message convention before this experiment; unified *behavior* (log-and-skip/reject), not unified *strings*, since the strings are read by a human during debugging, not parsed by anything |
| **Timing metrics** | New: one summary line per call — `wrote N files in Xms (skipped: A/B/C)` (`file_writer_service.py:559-560, 624-627`) | **None added.** `write_fix()` is called ~11 times per repair loop for single files; per-call timing would be noise (sub-millisecond, dominated by the caller's own loop), and the caller (`v6_orchestrator.py`) already tracks repair-loop-level timing separately. Adding it here would be scope creep with no evidence of value. | **Intentionally asymmetric** — justified by call-pattern difference (1 bulk call vs. N single-file calls), not an oversight |
| **Return contract** | Returns `base_dir` (str) unconditionally — success/failure is per-file (skip-and-continue), not per-call | Returns `bool` — `True`/`False` for this one file | **Unchanged, and correctly asymmetric** — the two functions answer different questions ("where did the project land" vs. "did this one fix apply"); this is call-site contract, not a validation gap, and out of scope to change (would break every one of the ~11+1 call sites) |
| **Shared module** | `app/utils/safe_path.py`, `app/utils/atomic_write.py` | `app/utils/safe_path.py`, `app/utils/atomic_write.py` | **Yes** — both new modules, both function-first (no classes), both pathlib-only, importable independently with no circular-import risk (neither imports from `app/services/*`) |

## What was deliberately NOT merged

Per Part 4's explicit instruction ("Do NOT merge unless behavior
remains identical"):

- **The two `_is_safe_to_write()` functions** stay separate — see the
  Syntax row above. `write_files()`'s version is strictly more capable
  (auto-repair attempts); collapsing them either loses that capability
  or silently adds new repair behavior to `write_fix()`, both out of
  bounds for this experiment.
- **The two rejection-logging message formats** stay as each
  function's own pre-existing convention — unifying wording is a
  cosmetic change with zero functional value and non-zero risk of
  breaking anything that might grep log output (not confirmed to
  exist, but not worth the risk for a $0-value change).
- **`write_files()`'s per-project-run summary metric** was NOT added
  to `write_fix()` — see the Timing row; the call patterns are
  different enough that the same instrumentation doesn't answer the
  same question in both places.

## What WAS merged (the actual fix)

- Path-traversal validation: **one function**, `resolve_safe_path()`,
  used by both. Closes Exp065 Finding #1 (`write_files()` had zero
  validation) and upgrades `write_fix()`'s narrower pre-existing
  string-based check to the same resolved-path-containment approach,
  gaining symlink/UNC/drive-letter coverage it didn't have.
- Semantic validation: **one function**, `_check_request_field_consistency()`
  (still physically defined in `fix_writer_service.py`, reused by
  `write_files()` via a function-local import — the same lazy
  cross-import pattern this file pair already used in the opposite
  direction for `_normalize_newlines()`, so this introduces no new
  circular-import risk).
- Atomic writes: **one function**, `atomic_write_text()`, used by
  both (and by every otherwise-direct `open(..., "w")` call inside
  `write_files()` — the `app/database.py` fallback and the
  frontend/PWA template-scaffold loop — even though those two call
  sites have static, non-LLM-controlled paths and therefore didn't
  need the traversal check, they still benefit from crash-safety at
  zero behavior-preserving cost).

## Update (Experiment 067): `_regenerate_module()` hardened, matrix extended

Exp067 brought `_regenerate_module()`'s one write call site to
partial parity with `write_files()`/`write_fix()`. Extended matrix:

| Dimension | `write_files()` / `write_fix()` (Exp066) | `_regenerate_module()` (Exp067) | Symmetric now? |
|---|---|---|---|
| **Path-traversal validation** | `resolve_safe_path()` | `_safe_patch_target()` (unchanged, pre-existing) | **No — intentionally not merged.** See below. |
| **Absolute-path handling** | Rejected unconditionally | **Allowed if it resolves inside the project root** (documented, load-bearing design — fix prompts echo back absolute diagnostic paths) | **Intentionally asymmetric** — these are different callers with different real input shapes, not an oversight |
| **Windows drive-absolute / UNC rejection** | Explicit, via `has_windows_drive_or_unc()` inside `resolve_safe_path()` | **Already correctly rejected** by `_safe_patch_target()`'s existing `os.path.isabs()` + containment check — confirmed empirically, not a gap Exp066 assumed it was | **Yes, functionally** (different mechanism, same outcome) |
| **Windows drive-relative rejection** (`C:evil.py`) | Explicit, via `has_windows_drive_or_unc()` | **New in Exp067**: `has_windows_drive_or_unc()` reused as a standalone check ahead of `_safe_patch_target()`, inside `_regenerate_module()`'s own loop | **Yes** — same underlying function, called from a second, independent site |
| **Symlink-escape rejection** | Via `resolve_safe_path()`'s `Path.resolve()` | Via `_safe_patch_target()`'s own `.resolve()` — same standard-library mechanism, independently implemented | **Yes in effect**, unverified live in both cases (same environment limitation) |
| **Atomic writes** | `atomic_write_text()` | `atomic_write_text()` — **new in Exp067**, was `target.write_text()` | **Yes** — same shared helper |
| **Rollback on write failure** | Temp-file cleanup + re-raise | Same (shared helper) — **but** the re-raised exception is caught by `_regenerate_module()`'s own pre-existing broad `except Exception`, which reroutes to `_apply_fix_group()` (a *different*, unhardened write path) rather than surfacing as a clean failure | **Partially — the atomic guarantee itself is symmetric; what happens after the exception is not**, and fixing that would mean changing repair-logic control flow, out of scope |
| **Semantic (request/field) validation** | `_check_request_field_consistency()` | **Not present** — `_regenerate_module()` never had this check and Exp067's mission didn't ask for it (semantic validation is a content check, not named in this "write path" mission) | **No — not attempted, arguably out of scope for a path/atomicity-focused experiment** |

**Why `_safe_patch_target()` was not replaced by `resolve_safe_path()`
(Part 2's explicit question)**: unsafe. Two independent reasons:

1. **Behavioral**: `_safe_patch_target()` deliberately allows an
   absolute path if it resolves inside the project root; `resolve_safe_path()`
   rejects all absolute paths unconditionally. A full swap would reject
   real fixes this call site currently accepts — a genuine regression,
   not a hardening.
2. **Blast radius**: `_safe_patch_target()` is called from 5 places in
   `orchestrator.py`, only 1 of which (`_regenerate_module()`) was
   named by this experiment's mission. Modifying the shared function
   itself would silently change write behavior at 4 other call sites
   this experiment was never asked to touch — a violation of "no
   refactoring outside this write path."

Instead, the one true, isolated gap empirical testing found (the
drive-relative shape) was closed via an additive, scoped check inside
`_regenerate_module()`'s own code, touching nothing shared.

## Remaining, intentionally out-of-scope asymmetry

`app/repair/orchestrator.py::_apply_fix_group()` — **4 separate write
call sites** (~line 404, 615, 635, 764), all still using the exact
legacy pattern (`_safe_patch_target()`, no drive/UNC check, direct
`target.write_text()`, no atomic write) that `_regenerate_module()`
itself had before this experiment. Neither Exp066's nor Exp067's
mission named this function. This is now the largest remaining
concentration of unhardened write call sites in the codebase (4 of the
9 total write call sites found across both experiments' audits) and
the clearest candidate for a future, explicitly-scoped Exp068.
