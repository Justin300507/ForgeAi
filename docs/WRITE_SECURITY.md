# Write Pipeline Security Review (Experiments 066-067)

2026-07-12. Threat model: the `{path, content}` pairs written to disk
originate from LLM output (initial generation and repairs) — untrusted
in the sense that a model can hallucinate, echo back a diagnostic
string verbatim, or (per `app/repair/orchestrator.py:32-33`'s own
comment) occasionally emit `"../.."`-style paths. `content` itself is
not sandboxed here (it's still executed as the generated app's own
source, same as before this experiment) — this review is specifically
about **where on disk a write can land**, and about the write
mechanism's own crash-safety.

## Findings, and where each is now handled

| # | Threat | Where checked | Verdict |
|---|---|---|---|
| 1 | `../` relative traversal (single and multi-segment) | `app/utils/safe_path.py::resolve_safe_path()` — resolved-path containment via `Path.resolve()` + `relative_to()`, not string matching | **Fixed** (was entirely absent in `write_files()`; was a weaker `startswith("..")` string check in `write_fix()` that a form like `"a/../../b"` can defeat since the raw string doesn't start with `..`) |
| 2 | Absolute paths (POSIX or host-OS) | `resolve_safe_path()` — `Path(candidate_str).is_absolute()` check, before any resolution | **Fixed** for `write_files()` (was absent). `write_fix()`'s old `os.path.isabs(norm)` already caught this on a POSIX host; **now explicit and host-OS-independent** |
| 3 | Windows drive-letter paths (`C:\x`, `C:x`) on a non-Windows host | `_has_windows_drive_or_unc()` via `PureWindowsPath.drive` — filesystem-free, runs identically regardless of host OS | **New protection.** Neither writer had this before — `os.path.isabs()` on a Linux host does not recognize `C:\x` as absolute, so a Linux-deployed ForgeAI process could previously have written a file literally named `C:\x` inside the project root (harmless) or, if any code path later treated it as a Windows path, worse. Closed for both writers. |
| 4 | UNC paths (`\\server\share\x`) | Same `PureWindowsPath.drive` check, plus the general containment check as a second layer (confirmed via `tests/reliability/test_exp066_write_pipeline_hardening.py::test_write_fix_now_also_rejects_unc_path`) | **New protection**, defense-in-depth (two independent checks both reject it) |
| 5 | Symlink escape (a symlink inside the project root pointing outside it) | `Path.resolve()` follows existing symlinks before the containment check | **Covered by design** (standard library behavior, not something this module invents). **Not verified by a live test in this dev environment** — `os.symlink()` requires admin/Developer Mode privileges Windows denies here (`WinError 1314`); the corresponding test skips gracefully rather than silently omitting coverage. Treat as "believed correct, unverified live" until run once on a privileged machine or CI (Linux CI runners create symlinks without special privilege). |
| 6 | Parent-directory auto-creation escaping the sandbox | `os.makedirs(full_path.parent, exist_ok=True)` now runs on an **already-validated** `full_path` (the return value of `resolve_safe_path`), not on `os.path.dirname(os.path.join(base_dir, path))` computed from the raw, unchecked path | **Fixed.** Before this experiment, `write_files()`'s `os.makedirs(os.path.dirname(full_path), exist_ok=True)` ran on a path built with zero validation — a malicious path would have had its parent directories created *outside* the project root before the file write itself even happened. |
| 7 | Overwrite protection | Neither writer refuses to overwrite an existing file — this is **intentional, unchanged design**, not a gap: `write_files()`'s job is to (re)write the entire project every generation, and `write_fix()`'s job is to *replace* a broken file with a repaired one. Confirmed via `test_write_files_duplicate_path_last_write_wins` / `test_write_fix_duplicate_writes_second_call_overwrites` — a duplicate/repeat path still simply overwrites, same behavior as before this experiment. | **No change made — verified behavior-preserving** |
| 8 | Temp files left behind after a write | `atomic_write_text()`'s `except Exception: os.remove(tmp_path)` before re-raising | **Verified** via `test_atomic_write_leaves_no_temp_files_behind_on_success`, `test_atomic_write_rolls_back_on_replace_failure`, `test_atomic_write_rolls_back_on_write_failure` — no leftover temp file in either the success or failure path |
| 9 | Unsafe rename | `os.replace()` (not `os.rename()`) — atomic on both POSIX and Windows, and unlike `os.rename()` on Windows, `os.replace()` succeeds even when the destination already exists (which every write here needs, since files are routinely overwritten) | **Was never using `os.rename`; no change in risk, atomic write is a strict improvement over the prior direct `open(path, "w")`** |
| 10 | Unsafe deletion | `write_files()`'s `shutil.rmtree(base_dir, ...)` (whole-project wipe before regeneration) and `write_fix()`'s flat-file-conflict `os.remove()` — both pre-existing, both operate on paths derived from `base_dir`/`project_path` (not raw LLM path input at the point of deletion) | **Out of scope — not LLM-path-controlled, not touched.** `shutil.rmtree`'s target is `base_dir` itself (project-name-derived, not per-file LLM input); the `os.remove(flat_conflict)` target is derived from `parent_dir` of an *already-validated* `full_path`. Neither is a new risk this experiment introduces or was asked to fix. |
| 11 | Unsafe archive extraction | Not found anywhere in the write pipeline — `write_files()`/`write_fix()` never extract a zip/tar. | **N/A — no such code path exists** |

## Where validation occurs (the actual decision)

**Path safety is checked once, at the very top of each write function,
before any content processing** (`file_writer_service.py:572-577`,
`fix_writer_service.py:328-331`) — not deferred to the point of the
actual `open()`/`os.replace()` call. This was a deliberate design
choice, not the only option, made for two reasons:

1. **Fail fast, minimize wasted work.** `_normalize_newlines()`,
   `_ensure_create_all()`, `ast.parse()`, and the semantic AST walk are
   all pure-CPU work on `content` — none of it should run for a file
   that's going to be rejected anyway on its `path` alone.
2. **No TOCTOU (time-of-check/time-of-use) gap.** `resolve_safe_path()`
   returns the resolved `Path` object itself, and every subsequent
   step (`os.makedirs`, `atomic_write_text`) operates on **that exact
   returned object** — not on a path re-derived from the original
   (unchecked) string later. There is no window where a second,
   unvalidated join could reintroduce the traversal after the check
   passed.

## `_regenerate_module()` (Experiment 067) — hardened, corrected findings from empirical testing

Exp066 documented this path's threat profile based on **reasoning by
analogy** to `write_fix()`'s old gap, without live-testing
`_safe_patch_target()` itself. Exp067 ran the actual attack strings
through the real function on this Windows host before touching
anything, and found the analogy didn't fully hold:

| Threat | Exp066's assumption | Exp067's empirical finding | Action taken |
|---|---|---|---|
| `../`, `../../`, multi-segment traversal | Untested, assumed equivalent to `resolve_safe_path` | **Confirmed correctly blocked** — `_safe_patch_target()`'s `os.path.normpath()` + `.relative_to()` containment check already catches every form tested | No change needed |
| Absolute POSIX path outside root (`/etc/passwd`) | Untested | **Confirmed correctly blocked** | No change needed |
| Windows drive-**absolute** path (`C:\evil.py`) | Assumed a gap ("same gap `write_fix()`'s old check had") | **Confirmed already blocked** on this host — `os.path.isabs()` recognizes this shape on Windows, so the existing containment check catches it. The Exp066 assumption was WRONG for this specific shape. | No change needed |
| UNC path (`\\server\share\evil.py`) | Assumed a gap | **Confirmed already blocked** — same reasoning, `ntpath.isabs()` treats a leading `\\` as absolute | No change needed |
| Windows drive-**relative** path (`C:evil.py`, no backslash) | Not distinguished from the absolute case | **Confirmed a real (non-escaping) gap**: `os.path.isabs("C:evil.py")` returns `False` on Windows, so it falls into the relative branch and silently lands inside the project root under a prefix-stripped name (`evil.py`). Never an escape — always stayed contained — but not the intended interpretation either. | **Fixed**: new `has_windows_drive_or_unc()` check added inside `_regenerate_module()`'s own loop, ahead of `_safe_patch_target()` |
| Empty path (`""`) | Not tested | Resolves to the project root itself (not a file) — harmless in practice (`target.write_text()`/`atomic_write_text()` would fail on a directory target), not exploitable | Not fixed — no `content` accompanies an empty path in the actual `fix_data["files"]` shape this loop consumes, and the pre-existing `if not rel or not content: continue` guard already skips it in every real code path |
| Symlink escape | Assumed equivalent (same `Path.resolve()` mechanism) | **Confirmed equivalent by code inspection**; live-verification blocked by the same environment limitation as Exp066 (no symlink-creation privilege on this Windows host) | Unverified live, same caveat as Exp066 |
| Duplicate writes | Not addressed | Confirmed same last-write-wins behavior as `write_files()`/`write_fix()` — `modified.append(rel)` records both entries; the second `atomic_write_text()` call simply overwrites | No change — verified behavior-preserving |
| Atomicity | Flagged as a gap | **Fixed**: `target.write_text(content, encoding="utf-8")` replaced with `atomic_write_text(target, content)` | Fixed |

**Correction to Exp066's own record**: `docs/WRITE_SECURITY.md`'s
original text asserted `_safe_patch_target()` had "the same gap
`write_fix()`'s old check had" for Windows-drive/UNC paths. Empirical
testing in Exp067 shows this was not accurate — `_safe_patch_target()`
was already more robust than assumed for the absolute-shaped forms of
those attacks; only the obscure drive-relative form had a real (if
non-escaping) gap. Recorded here rather than silently corrected, per
this session's own "Unknown means Unknown, verify don't guess"
discipline — the lesson is to empirically test a claim before reusing
it as the basis for a new experiment's premise.

**Why `_safe_patch_target()` itself was not modified**: it is called
from 5 places in `orchestrator.py`, not just `_regenerate_module()` (4
more inside `_apply_fix_group()`, none named in Exp067's mission).
Modifying the shared function would touch write behavior across all 5
call sites — outside the explicit "only harden the write path [named
in the mission]" and "no refactoring outside this write path" rules.
The new drive/UNC check was instead added as a narrow, additive guard
inside `_regenerate_module()`'s own loop body only.

## Residual write paths not covered by either experiment

`app/repair/orchestrator.py::_apply_fix_group()`'s 4 write call sites
(~line 404, 615, 635, 764) still use the exact legacy pattern
`_regenerate_module()` had before Exp067: `_safe_patch_target()` (no
drive/UNC check) + direct `target.write_text()` (no atomic write, no
rollback). Neither Exp066 nor Exp067's mission named this function.
**Confirmed residual gap, flagged for a future experiment, not fixed.**

## Summary verdict

`write_files()` and `write_fix()` (Exp066) share one centralized,
pathlib-only traversal guard and one atomic-write helper.
`_regenerate_module()` (Exp067) now shares the atomic-write helper and
gained a scoped defense-in-depth check for the one real (if
non-escaping) gap empirical testing found in its existing path guard —
but deliberately kept its own, different `_safe_patch_target()`
validator rather than adopting `resolve_safe_path()` wholesale, because
that function's absolute-path-if-contained allowance is real,
documented, load-bearing behavior for how fix prompts actually work.
Three of five write call sites now share the atomic-write helper; two
categories remain unhardened and explicitly out of scope for both
experiments to date: `_apply_fix_group()`'s 4 call sites (no experiment
has named this function) and live symlink verification (blocked by
this dev environment's lack of symlink-creation privilege, not a code
gap).
