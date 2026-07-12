# Experiment 085 — Extend Auth Completeness with Cross-File Request Validation

2026-07-12. Offline, $0, zero Cerebras calls. Implements Exp084's
recommended correction: extends Exp064's existing field-consistency AST
machinery with cross-file resolution, wired into
`check_auth_completeness()`.

## 1. Code diff

**`backend/app/services/fix_writer_service.py`**:

- `_check_request_field_consistency(path, content)` → `(path, content,
  project_path=None)`. When `project_path` is omitted (every existing
  caller, i.e. `write_fix()`), behavior is byte-for-byte identical to
  before — no cross-file work happens at all.
- New `_resolve_import_module_path(project_path, module)`: resolves a
  dotted, project-local absolute import (`app.schemas.auth`) to its file
  path, reusing the existing `resolve_safe_path` helper (already imported
  in this file) rather than hand-rolling path-join safety. Returns `None`
  for anything else (external packages, missing files) — conservative by
  construction.
- New `_collect_cross_file_basemodel_classes(project_path, tree)`: for
  every `from app.X.Y import Name[, Other as Alias]`, resolves the target
  file and reuses `_collect_basemodel_classes` (Exp064's own function,
  unmodified) on it. Only adds classes not already found locally — a
  same-file definition always wins if both somehow exist.
- The main function now merges `classes = same_file | cross_file` (local
  wins) before running the exact same attribute-access walk as before.
  The rejection message now says "imported into this file" vs. "defined
  in this same file" depending on where the class was actually resolved,
  so the diagnostic stays accurate either way.

**`backend/app/repair/auth_completeness.py`**:

- `AuthCompletenessResult` gains `field_mismatches: list = field(default_factory=list)`.
- `check_auth_completeness()`, after confirming the router is wired
  (previously the last check before `complete = True`), now also runs
  `_check_request_field_consistency(rel_file, content, project_path=str(root))`
  against every file that actually defines a required/recommended auth
  endpoint (not every `.py` file in the project — scoped precisely to
  auth-relevant files, matching this experiment's own scope constraint).
  A confirmed mismatch sets `complete = False` with the specific reason;
  no mismatch falls through to the existing `complete = True` path
  unchanged.
- `ensure_auth_completeness()` itself is **unchanged** — it already
  calls `_patch_auth_routes()` unconditionally whenever
  `check_auth_completeness()` reports incomplete, for whatever reason.
  Since `_patch_auth_routes()` fully replaces `auth_routes.py` with the
  inline, known-good template (which never imports an external schema),
  the new failure mode is already covered by the existing repair
  mechanism — zero additional repair logic needed.

**Deliberately not done**, per constraints: `skip_protected_injections`
untouched, no auth-template re-injection logic changed, no new/parallel
validator module — this is a strict extension of Exp064's existing
functions, reused by a new caller.

## 2. Regression results

New test file
`backend/tests/reliability/test_exp085_cross_file_auth_validation.py`
(12/12 pass): same-file behavior identical whether `project_path` is
given or not (Task 4), imported-schema matching/mismatching (Task 1/2),
unrelated imports and unresolvable imports correctly ignored (no false
positives), external-package imports never mistaken for project schemas,
and `check_auth_completeness()` integration — reports incomplete only on
a verified mismatch (Task 3), stays complete when fields match, and
never runs the field check before wiring is confirmed (an endpoint/wiring
gap is reported first, field-consistency only ever adds an additional
reason to reject, never masks a more fundamental one).

Existing Exp064 suite (`test_semantic_write_validation.py`) re-run: 22/24
pass — same 2 pre-existing failures from prior cycles (unrelated content-
corruption replay tests), confirming the same-file write-time guard used
by `write_fix()` is untouched.

Full `backend/tests/reliability/` suite (50 files, one new): **47/50
pass**. Same 3 pre-existing, unrelated failures as Exp082's cycle
(`test_exp066_write_pipeline_hardening.py`, `test_exp070_security_phase0.py`
— missing `jose` module, an environment gap — `test_semantic_write_validation.py`'s
2 known failures). No new failures introduced.

## 3. Offline replay of the known auth failure

Reconstructed the exact Exp084-confirmed failure shape: a `SignupRequest`
defined in `app/schemas/auth.py` (imported into `auth_routes.py`, not
inline — the architecture's natural layout) missing the `.username` field
the handler accesses, with an unwired-vs-wired main.py and a `user.py`
model present (satisfying `_patch_auth_routes`'s own gate).

```
BEFORE:
  complete: False
  reason: request-field mismatch: app/routes/auth_routes.py:8:
    'req.username' accessed in signup(), but class SignupRequest
    (imported into this file) has no field 'username' --
    declared fields: ['display_name', 'email', 'password']

ensure_auth_completeness() repair:
  [patcher] Injected known-good app/routes/auth_routes.py
  status: repaired

AFTER:
  complete: True
  reason: complete
```

Confirms the full detect → repair → verify cycle now works end-to-end
for exactly the bug shape that previously had a 0% self-heal rate.

## 4. Estimated reliability improvement

Per Exp083's measurement: this exact error class accounted for 9/30
(30%) of the last 30 generations and 53% of that window's failures, all
0% self-healing. With this fix, any generation that hits it should now
resolve automatically the moment `ensure_auth_completeness()` next runs
(inside the same Architecture Repair block that previously left it
broken) — no additional fix-loop attempts, no LLM cost. Projected
last-30 success-rate improvement: up to **+30 percentage points**
(43.3% → ~73.3%), matching Exp083's original estimate, now backed by an
actual implemented and offline-verified fix rather than a projection.

## 5. Recommendation for Exp086

**Live-validate next.** Run a canary against `benchmarks/golden/01_todo.txt`
(the exact idea text behind all 9 historical failures) — ideally several
consecutive attempts, or one that happens to trigger Architecture Repair,
since the bug is conditional on that code path firing, not guaranteed
every run. Confirm: (a) if Architecture Repair fires and touches
`auth_routes.py`/its schema, `check_auth_completeness()` now correctly
flags it and `ensure_auth_completeness()` repairs it in the same cycle;
(b) the generation's final score and `succeeded` status reflect the
fix (no longer capped at 70.7–74.4 by this specific bug); (c) no
regression in normal (non-Architecture-Repair) runs, where this new check
should simply report `complete` as before.

**Deliverables**: this doc, `experiments.md` entry, code diff in
`backend/app/services/fix_writer_service.py` and
`backend/app/repair/auth_completeness.py`, new test file
`backend/tests/reliability/test_exp085_cross_file_auth_validation.py`.
**Cost: $0, zero Cerebras calls.**
