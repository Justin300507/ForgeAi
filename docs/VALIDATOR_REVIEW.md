# Validator Subsystem Review (Experiment 059, Part 3)

2026-07-12. Offline, read-only review. Scope inspected: `app/verification/engine.py`
(1692 lines, the top-level `VerificationEngine`), `app/services/validator_service.py`
(1184 lines, `validate_project()` + orchestration), 13 standalone
`app/services/*_validator.py` files (74-267 lines each: `architecture_validator.py`,
`database_validator.py`, `duplicate_class_validator.py`, `endpoint_validator.py`,
`global_statement_validator.py`, `orm_validator.py`, `router_export_validator.py`,
`schema_model_validator.py`, `self_shadow_validator.py`, `session_validator.py`,
`stub_handler_validator.py`, `symbol_validator.py`, `undefined_symbol_validator.py`),
`app/runtime/user_journey_runner.py` (976 lines), `app/services/runtime_validator_service.py`
(65 lines), `app/contract/validator.py` (122 lines), `app/runtime/{deployment_validator,docker_validator,vision_validator}.py`.

## Duplicated validators

`Unknown` for a full pairwise cross-file semantic-duplication comparison
of all 20 validator files — not completed in the time available. One
confirmed structural near-duplicate: `app/services/orm_validator.py:117`
(`validate_orm_usage`) and `app/services/schema_model_validator.py` both
independently walk the SQLAlchemy/Pydantic class AST via their own
`ast.parse` + `ast.ClassDef` pass, and `app/services/duplicate_class_validator.py:32`
does its own third, independent `ast.ClassDef` walk over the same project
tree. Three separate files each doing their own `os.walk` + `ast.parse`
+ `ClassDef` inspection pass rather than sharing one AST-walk utility —
this is both a duplication finding and a performance finding (see Part
5, `docs/PERFORMANCE_FINDINGS.md`, Finding 1 — the same redundant-scan
pattern generalizes across ~20 validator functions, not just these 3).

## Shared infrastructure — the core finding

The validator subsystem uses **four incompatible result shapes**, no
shared base type or protocol:

1. **`Diagnostic` dataclass** (`app/core/context.py:65`) —
   `error_id, category, severity, source, message, file_path, line_number,
   stack_trace, fix_hint, related_ids, metadata`. Used throughout
   `verification/engine.py` (lines 109, 126, 299, 468, 761, 912, 1282).
2. **Plain f-strings appended to a shared `errors: list`** — every one of
   the 13 standalone validators (`validate_X(project_path, errors)`
   convention). Examples: `duplicate_class_validator.py:70`
   (`errors.append(f"Duplicate class definition: '{class_name}'...")`),
   `database_validator.py:15` (`errors.append("Missing app/database.py")`),
   `router_export_validator.py:90`, `global_statement_validator.py:34`,
   `self_shadow_validator.py:73`, `session_validator.py:46/71`,
   `stub_handler_validator.py:74`, `undefined_symbol_validator.py:171`,
   `orm_validator.py:45/52/59/158`. No severity, no category, no
   structured file/line — everything baked into prose.
3. **`JourneyStep` dataclass** (`app/runtime/user_journey_runner.py:16`)
   — `name, passed, duration_ms, detail` — a third, incompatible shape.
4. **Raw `dict`** returned from `validate_runtime()`
   (`app/services/runtime_validator_service.py:57`) — a fourth shape.

**This has already caused a confirmed live bug, not a hypothetical
risk.** `app/verification/engine.py:1641-1662`
(`_filepath_static`/`_extra_filepaths_static`) exists specifically to
**regex-parse the file path back out of the plain-string errors**
`validate_project()` produces, because `Diagnostic.file_path` is left
unset for all of them. That function's own docstring (lines 1646-1656)
documents the real, previously-observed incident this caused: a
diagnostic like `"Frontend auth field mismatch: src/pages/RegisterPage.jsx
POSTs to..."` named the broken file in prose, but the repair grouper's
`Diagnostic` had no `file_path`, so the LLM fixed the wrong file
(`app/schemas/user.py`) and the bug recurred, unchanged, across every fix
attempt. This is a real, already-manifested reliability cost of the
4-shape inconsistency.

## Failure categorization

Not consistent. `Diagnostic.category` is a real enum (`ErrorCategory`,
`app/core/context.py`). The 13 standalone validators have no category
field at all — their failure "class" exists only as a substring of the
prose message. `Unknown` exactly how/where `verification/engine.py`
re-derives a category for these downstream (not confirmed in detail this
pass).

## Unreachable code

None found among the 13 standalone validators — each of
`validate_architecture`, `validate_database`,
`validate_duplicate_class_definitions`, `validate_module_level_global`,
`validate_no_flask_sqlalchemy`, `validate_orm_usage`,
`validate_router_exports`, `validate_self_shadowing_functions`,
`validate_session_management`, `validate_stub_handlers`,
`validate_undefined_symbols` is imported and called exactly once from
`validator_service.py` (confirmed via grep — no dead files). `Unknown`
for `verification/engine.py`'s internal 1692 lines — not fully audited
for dead branches this pass.

## Missing logging

All 13 standalone validators: **zero `print()`/log statements**
(confirmed via `grep -c "print("` = 0 for every one). They communicate
exclusively by mutating the shared `errors` list — a developer debugging
a stuck validation has no signal that e.g. `validate_orm_usage` even ran,
let alone what it inspected, until reading the final `errors` list.
Contrast: `verification/engine.py` prints stage-level progress throughout.

## Missing timing

`verification/engine.py` times itself extensively and consistently —
`t0 = time.time()` at 14+ locations (lines 56, 102, 259, 419, 509, 555,
661, 863, 945, 1005, 1063, 1109, 1150, 1216) plus per-request latency
tracking (1073-1076). `user_journey_runner.py:336-341` times every
individual `JourneyStep`. **None of the 13 standalone validators, and
`validator_service.py`'s own `validate_project()` orchestrator, have any
timing instrumentation** (confirmed via grep for `time.time()`/
`time.perf_counter()` — zero hits across all 14 files). Since
`validate_project()` runs all 13 checks inside one stage timed only in
aggregate by the caller (`verification/engine.py:59`), there's no way to
identify which specific sub-validator is slow if `static-validation`'s
duration regresses.

## Recommendations (documentation only — nothing implemented)

Ranked by risk:

1. **Unify the 4 result shapes into `Diagnostic`.** Highest-risk item —
   already caused a confirmed live bug (wrong-file fix from a missing
   `file_path`). The 13 standalone validators are the cheapest to
   migrate: mechanical change from `errors.append(f"...")` to
   `errors.append(Diagnostic(...))`, since they already isolate message
   construction at the append call site.
2. **Add per-validator timing** to `validator_service.py`'s dispatch of
   the 13 sub-validators — trivial (wrap each call with a `t0`/`elapsed`
   pair), would immediately surface which check dominates
   `static-validation`'s duration.
3. **Add minimal logging** (one line per validator: name + pass/fail +
   error count) to the 13 silent validators — cheap, high debuggability
   payoff.
4. **Consolidate the repeated `os.walk`/`ast.parse` tree traversal**
   happening independently across `orm_validator.py`,
   `duplicate_class_validator.py`, `undefined_symbol_validator.py`,
   `self_shadow_validator.py` (and likely others — see
   `docs/PERFORMANCE_FINDINGS.md` Finding 1 for the full ~20-call-site
   picture) into one shared AST-cache pass.
5. **Formalize `Diagnostic` as the required shape for any NEW validator**
   going forward — a one-line convention note (e.g. in `CLAUDE.md` or a
   short CONTRIBUTING note) would prevent a 5th ad hoc shape from
   appearing before the 4 existing ones are unified.
