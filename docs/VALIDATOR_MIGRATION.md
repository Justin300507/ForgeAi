# Validator Migration Table (Experiment 060)

2026-07-12. Per-validator migration status. See `docs/VALIDATOR_CONTRACT.md`
for the design; this document is the inventory and the evidence.

## Migrated (15 functions, 13 files) — old shape → canonical Diagnostic

| Validator function | File | Old shape | Category | Severity | file_path source |
|---|---|---|---|---|---|
| `validate_frontend_auth_fields` | `validator_service.py` | plain string | CONTRACT | HIGH | validator-computed (`rel(...)`) — **the exact function that caused the confirmed production bug** |
| `validate_undefined_symbols` | `undefined_symbol_validator.py` | plain string | IMPORT | HIGH | validator-computed |
| `validate_architecture` | `architecture_validator.py` | plain string | CONTRACT | MEDIUM | none (project-level check) |
| `validate_orm_usage` | `orm_validator.py` | plain string | CONTRACT | HIGH | validator-computed |
| `validate_no_flask_sqlalchemy` | `orm_validator.py` | plain string | CONTRACT | MEDIUM | validator-computed |
| `validate_database` | `database_validator.py` | plain string | CONTRACT | MEDIUM (CRITICAL for the "missing app/database.py" case, set in `validate_project()` itself) | validator-computed |
| `validate_router_exports` | `router_export_validator.py` | plain string | CONTRACT | HIGH | validator-computed |
| `validate_schema_model_consistency` | `schema_model_validator.py` | plain string | CONTRACT | MEDIUM | validator-computed |
| `validate_session_management` | `session_validator.py` | plain string | CONTRACT | MEDIUM | validator-computed |
| `validate_self_shadowing_functions` | `self_shadow_validator.py` | plain string | CONTRACT | MEDIUM | validator-computed |
| `validate_stub_handlers` | `stub_handler_validator.py` | plain string | CONTRACT | MEDIUM | validator-computed |
| `validate_module_level_global` | `global_statement_validator.py` | plain string | CONTRACT | MEDIUM | validator-computed |
| `validate_duplicate_class_definitions` | `duplicate_class_validator.py` | plain string | CONTRACT | MEDIUM | none (spans multiple files — full list in `metadata.locations`) |
| `validate_endpoints` | `endpoint_validator.py` | plain string | API | MEDIUM | validator-computed (`expected_file`) |
| `validate_frontend_api_calls` | `endpoint_validator.py` | plain string | API | MEDIUM | validator-computed (the frontend file that exists; `metadata.expected_backend_file` for the one that doesn't yet) |
| `validate_orphan_routes` | `endpoint_validator.py` | plain string | CONTRACT | MEDIUM | validator-computed |

Plus the two inline checks in `validate_project()` itself (not
previously separate "validators" but the same string-append pattern):
"Missing app/main.py" (CRITICAL) and "Missing route file: ..." (HIGH),
both now also migrated.

Every category/severity assignment above is an **exact parity match**
with what `verification/engine.py`'s pre-existing
`_categorise_static`/`_severity_static` regex heuristics already assign
to that message text — chosen deliberately for zero behavior change, not
re-derived. See the inline code comment at each call site.

## Not migrated this cycle — still on the legacy string-only path (fully functional, unaffected)

| Validator function | File | Why not migrated this cycle |
|---|---|---|
| `validate_backend_imports` | `validator_service.py` | Scope cut — see "Scope decision" below |
| `validate_imported_symbols` | `validator_service.py` | Scope cut |
| `validate_frontend_imports` | `validator_service.py` | Scope cut |
| `validate_frontend_nav_targets` | `validator_service.py` | Scope cut |
| `validate_frontend_api_client` | `validator_service.py` | Scope cut |
| `validate_route_quality` | `validator_service.py` | Scope cut |
| `validate_requirements` | `validator_service.py` | Scope cut |
| `validate_common_antipatterns` | `validator_service.py` | Scope cut |
| py_compile syntax-error check | `validator_service.py`, inline in `validate_project()` | Scope cut (simple, low-risk, good Exp061 candidate — file_path and CRITICAL severity are trivially available) |

These are **not broken or degraded** — `verification/engine.py`'s
adapter boundary falls back to the exact same regex-based construction
these errors have always gone through. `test_engine_falls_back_to_regex_for_unmigrated_validators`
confirms this explicitly: an unmigrated validator's error still produces
a valid `Diagnostic`, never a crash or a silently-dropped entry.

**Scope decision, stated honestly:** 24 validator-ish call sites exist
in `validate_project()` total (confirmed by direct read, more than
Exp059's fork estimate of 13 "standalone files" — that count missed 9
validators defined directly inside `validator_service.py` itself). This
experiment migrated the 15 functions across the 13 separate
`*_validator.py` files (Exp059's own scoped, pre-assessed "mechanical"
set) plus the one function that caused the confirmed production bug
(`validate_frontend_auth_fields`, which lives in `validator_service.py`
itself). The remaining 8 `validator_service.py`-internal functions were
left on the legacy path — migrating them follows the exact same pattern
already proven 15 times over in this experiment, and is a natural,
low-risk Exp061 candidate, not attempted here to keep this cycle's
change set reviewable and within a single session's rigor budget.

## The other 2 of Exp059's "4 shapes" — investigated, found already adapted, not touched

- **`JourneyStep`** (`app/runtime/user_journey_runner.py:16`) and
- **raw `dict`** (`app/services/runtime_validator_service.py`'s `validate_runtime()` return)

Investigated by reading `verification/engine.py:673-810`
(`_run_runtime_validation`) directly: this function **already**
constructs one canonical `Diagnostic` per runtime/journey failure
(`engine.py:774-787`), fully populated with `category`, `severity`,
`file_path`, `stack_trace`, `fix_hint`, `metadata` — pulled from the
journey/runtime data at the point of consumption. This is a complete,
already-correct adapter that predates this experiment; it was not built
by this experiment, and does not need to be, because it already
satisfies the "future engineer shouldn't care which validator produced
it" goal for this data. `run_user_journey` itself (the actual highest
complexity/depth function in the repo, per `docs/ENGINEERING_REVIEW.md`
Part 1) was **not modified** — this experiment's own rules ("do not
refactor unrelated code", "do not rewrite validation logic") and Exp059's
own risk flagging of that specific function ruled out touching its
internals, and doing so was unnecessary given the consumer-side adapter
already works.

## Verification

**Behavioral parity (git stash before/after):** stashed all Exp060
changes, ran `validate_project()` against a real generated project
(`generated_projects/todo_list_app`, the same project Exp056/058's live
canary runs used) — captured the exact `errors` list. Popped the stash,
re-ran against the SAME project — `errors` list was **byte-identical**,
same 3 strings, same order. `diagnostics` key absent pre-migration,
present and populated post-migration with matching content.

**Real fixture end-to-end**: `validate_project()` and
`_run_static_validators()` both run cleanly against the real
`todo_list_app` fixture, producing 3 `Diagnostic` objects
(`validate_schema_model_consistency`) with `file_path` matching what the
old regex extraction would have found for this specific message shape
(no discrepancy for this case — confirmed by direct comparison).

**Regression tests**: 23 new tests in
`tests/reliability/test_validator_contract_unification.py`, covering:
- Every migrated validator type (11 of the 15 spot-checked individually
  with a real triggering fixture; the remaining 4 share identical
  category/severity-assignment code paths already covered by the
  spot-checked ones)
- Missing `file_path` (`validate_architecture`, `validate_duplicate_class_definitions`)
- Backward compatibility: every migrated validator still works called
  the pre-migration way (2 args, no `diagnostics`)
- `errors` list byte-identical regardless of whether `diagnostics` is
  requested
- Multi-validator aggregation in one `validate_project()` call
- Serialization: `Diagnostic` → `dataclasses.asdict()` → `json.dumps()` →
  `json.loads()` round-trips cleanly, including the new optional fields'
  `None` defaults and the `str`-subclassed `ErrorCategory`/`ErrorSeverity`
  enums serializing as their plain string values
- `verification/engine.py`'s adapter boundary: prefers a native
  `Diagnostic` when available, falls back to the exact pre-existing
  regex path for anything not migrated
- Observatory compatibility: `compute_observatory` runs unaffected
  (confirmed it never touches live `Diagnostic` objects)

**Full suite**: all 49 test files (48 pre-existing + this experiment's
new file) plus `tests/adr002/test_orchestrator_wiring.py` pass, run
before and after every individual file change during this migration, not
just once at the end.

## Files changed (14 production files, 1 new test file)

```
backend/app/core/context.py                              (Diagnostic extended, 6 new optional fields)
backend/app/services/validator_service.py                (validate_frontend_auth_fields migrated;
                                                            validate_project() threads diagnostics through)
backend/app/services/undefined_symbol_validator.py        (migrated)
backend/app/services/architecture_validator.py            (migrated)
backend/app/services/orm_validator.py                     (migrated, 2 functions)
backend/app/services/database_validator.py                (migrated)
backend/app/services/router_export_validator.py           (migrated)
backend/app/services/schema_model_validator.py            (migrated)
backend/app/services/session_validator.py                 (migrated)
backend/app/services/self_shadow_validator.py             (migrated)
backend/app/services/stub_handler_validator.py             (migrated)
backend/app/services/global_statement_validator.py        (migrated)
backend/app/services/duplicate_class_validator.py         (migrated)
backend/app/services/endpoint_validator.py                (migrated, 3 functions)
backend/app/verification/engine.py                        (adapter boundary: prefer native Diagnostic)
backend/tests/reliability/test_validator_contract_unification.py  (new, 23 tests)
```

No other file was touched. Not committed, per this experiment's explicit
instruction.
