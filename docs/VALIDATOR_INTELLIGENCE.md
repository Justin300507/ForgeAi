# ForgeAI Validator Intelligence (Experiment 069, Part 7)

2026-07-12. Direct code investigation of every validator file. **The
most important finding of this document: there are 14 validator files,
not the ~12 assumed at the start of this investigation** — the
briefing missed `import_validator.py` and `symbol_validator.py`, and
both turned out to be dead code (see below).

## Architecture correction

`validator_service.py` is **not a pure orchestrator** — it directly
contains 9 of its own validation function implementations
(`validate_backend_imports`, `validate_imported_symbols`,
`validate_frontend_imports`, `validate_frontend_nav_targets`,
`validate_frontend_api_client`, `validate_frontend_auth_fields`,
`validate_route_quality`, `validate_requirements`,
`validate_common_antipatterns`) in addition to dispatching to the 12
separate validator files. It mixes orchestration and implementation in
one 1000+-line file.

## Full dispatch chain

Read directly from `validate_project()` (`validator_service.py:1014-1200`),
**23 validator calls in this exact order**: inline `main.py`/route-file
existence checks → `validate_backend_imports` → `validate_imported_symbols`
→ `validate_router_exports` (`router_export_validator.py`) →
`validate_database` (`database_validator.py`) → `validate_architecture`
(`architecture_validator.py`) → `validate_endpoints` +
`validate_frontend_api_calls` + `validate_orphan_routes`
(`endpoint_validator.py`, 3 functions) → `validate_undefined_symbols`
(`undefined_symbol_validator.py`) → `validate_self_shadowing_functions`
(`self_shadow_validator.py`) → `validate_orm_usage` +
`validate_no_flask_sqlalchemy` (`orm_validator.py`, 2 functions) →
`validate_session_management` (`session_validator.py`) →
`validate_schema_model_consistency` (`schema_model_validator.py`) →
`validate_frontend_imports`/`validate_frontend_nav_targets`/
`validate_frontend_api_client`/`validate_frontend_auth_fields`
(`validator_service.py` itself) → `validate_stub_handlers`
(`stub_handler_validator.py`) → `validate_module_level_global`
(`global_statement_validator.py`) → `validate_duplicate_class_definitions`
(`duplicate_class_validator.py`) → `validate_route_quality`/
`validate_requirements`/`validate_common_antipatterns`
(`validator_service.py` itself).

## Confirmed dead code: `import_validator.py` and `symbol_validator.py`

**147 combined lines, confirmed orphaned.** Grepped the entire `app/`
tree for any import of either module: the only hit is
`undefined_symbol_validator.py` importing from itself (a false
positive from the grep pattern, not a real cross-file dependency).
Neither `collect_imports()` (`import_validator.py:5`) nor
`build_symbol_table()` (`symbol_validator.py`) appears anywhere in
`validate_project()`'s dispatch chain or anywhere else in the
codebase. **12 of the 14 validator files are wired in; 2 are dead.**

## Per-validator table

| Validator | Lines / functions | Purpose | Consumer | Coverage impression |
|---|---|---|---|---|
| `endpoint_validator.py` | 297 / 5 | Detects missing/orphan endpoints, frontend-calls-nonexistent-backend | `validator_service.py` | The detector for this project's own **largest failure cluster** (`MissingEndpoint`, 48 instances per Experiment 068) — has **zero dedicated test coverage found**, a significant gap given its importance |
| `schema_model_validator.py` | 286 / 4 | Schema/model field consistency | `validator_service.py` | Not independently scored this cycle |
| `undefined_symbol_validator.py` | 187 / 2 | Catches undefined names | `validator_service.py` | Partial coverage of `AttributeError`'s detection story (see `docs/RUNTIME_KNOWLEDGE_BASE.md`) |
| `orm_validator.py` | 186 / 5 | SQLAlchemy/Flask-SQLAlchemy misuse | `validator_service.py` | Confirmed dedicated, part of the best-covered failure family (`SQLAlchemyError`) |
| `architecture_validator.py` | 140 / 3 | Architecture-plan conformance | `validator_service.py` | Not independently scored this cycle |
| `database_validator.py` | 138 / 2 | Database config/usage checks | `validator_service.py` | Confirmed dedicated, same family as `orm_validator.py` |
| `router_export_validator.py` | 111 / 1 | Router export-name checks | `validator_service.py` | Unknown whether distinct from `_patch_router_export_mismatch` (its own repair-side counterpart) or combined in spirit |
| `symbol_validator.py` | 106 / 2 | **Dead code** — orphaned | None | N/A |
| `session_validator.py` | 97 / 2 | Session management checks | `validator_service.py` | Top line is a leftover LLM-authoring artifact, not real code comment: `# Add this check inside validate_session_management, in app/services/session_validator.py` — a small but real documentation-hygiene finding |
| `stub_handler_validator.py` | 96 / 1 | Catches unimplemented handler bodies | `validator_service.py` | Not independently scored this cycle |
| `self_shadow_validator.py` | 95 / 1 | Catches shadowed builtins/modules | `validator_service.py` | Part of `AttributeError`'s partial detection story |
| `duplicate_class_validator.py` | 91 / 1 | Catches duplicate class definitions | `validator_service.py` | Not independently scored this cycle |
| `global_statement_validator.py` | 54 / 1 | Module-level global-statement checks | `validator_service.py` | Not independently scored this cycle |
| `import_validator.py` | 41 / 1 | **Dead code** — orphaned | None | N/A |

## Documentation quality (all 14 files)

**None has a real module-level docstring** — every file jumps straight
from a one-line path comment (present in only some files) directly
into imports. This is a consistent, repo-wide pattern, not isolated to
one file.

## Consumers

Every wired validator's sole confirmed consumer is
`validator_service.py::validate_project()`, itself called from
`app/verification/engine.py::_run_static_validators()`
(`verification/engine.py:52`).

## Test coverage, overall

Only `test_validator_contract_unification.py` targets the validator
layer generally (the sole cross-subsystem integration test found for
this system, per this project's own prior Experiments 060/065 work).
**No test file exists by name for any of the 12 individually wired
validator files.**

## Limitations / known gaps

Beyond the two dead files: whether any of the 12 wired validators has
an early-return that silently skips validation under some condition
was **not confirmed** within this cycle's time budget — flagged as
Unknown rather than assumed clean, consistent with this experiment's
own evidence-only rule.

## History

Individual per-file "which experiment added/changed this" attribution
was not traced exhaustively this cycle — cross-reference
`docs/ENGINEERING_HISTORY.md`'s timeline for validator-touching
experiments (024/025 for relationship validation, 040 for symbol
validation, 060/061 for the contract-unification work).
