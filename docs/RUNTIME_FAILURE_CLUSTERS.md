# Runtime Failure Clusters (Experiment 068, Parts 1-4)

2026-07-12. Offline, read-only, no fixes applied. Evidence only — every
number below is sourced from a specific file, cited inline. "Unknown"
means genuinely not established by the data available this cycle, not
a guess.

## Part 1 — Data sources actually collected

| Source | What it gave us | Volume |
|---|---|---|
| `backend/failure_memory/patterns.json` | The canonical failure taxonomy: 21 named pattern keys with count/first_seen/last_seen/stage, backed by `app/memory/failure_memory.py::classify_failure()` | 440 total runs since 2026-06-21 |
| `backend/failure_memory/generation_log.jsonl` | Per-run ledger: dominant_errors, fix_count, succeeded, final_score, prevention_counts, bundle_refs | 87 lines, but only reliable from 2026-07-06 onward — an earlier bug silently broke this log before that date (documented in this project's own prior-experiment history, not re-litigated here) |
| `backend/failure_memory/bundles/*.json` | Forensic per-failure detail: request/response/stderr/commit_sha/provider | 14 bundles, all `JourneyCRUDFailure`, all dated 2026-07-11 |
| `backend/benchmark_results/canary_history.json` | 32 labeled canary runs (fixed 3-app canary: todo/blog_cms/crm, plus a 4th "inventory" app added at run 31) with per-app build_ok/runtime_ok/crud_ok/browser_ok/forge_score/fix_attempts | 32 runs, 2026-07-06 through 2026-07-12 |
| `backend/failure_memory/strategy_outcomes.json` | Repair-strategy success/tries by error-category bucket (coarser than the 21-pattern taxonomy — buckets are `AttributeError`, `ConfigAttributeError`, `ImportError`, `SyntaxError`, `api`, `browser`, `contract`) | 7 buckets |
| `app/memory/reliability_metrics.py` (run directly, not reimplemented) | `compute_observatory()`, `compute_experiment_attribution()`, `compute_reliability_timeline()` — this project's own existing, already-trusted aggregation logic | Computed live this cycle over the above sources |
| `backend/scripts/failure_report.py` (run directly) | The existing reliability dashboard — reused, not rebuilt, per this project's standing "measure before build" convention | Computed live this cycle |
| Root-level `m*_canary_*_run.log` files (~30 files) | Raw canary transcripts — used as corroboration for specific experiment attributions, not independently re-parsed in full | Referenced, not exhaustively mined |
| `backend/failure_memory/cost_log.json` | Per-run LLM cost/token/time — 388 entries; **not reliably joinable to specific failure classes** (no shared key beyond project_name+timestamp proximity), used only for aggregate cost context | 388 entries |

**Not usefully minable this cycle, noted rather than silently skipped:** `generated_projects/` (mostly `.zip` archives, a handful of unzipped dirs with no distinguishing runtime-error artifacts beyond what's already captured in the sources above — checked, not a source of new evidence). `backend/llm_cache/` (5,901 cached response files — a cache of LLM I/O, not a failure log; scanning it for failure evidence would mean re-deriving what generation_log.jsonl and patterns.json already extracted, out of proportion to this cycle's time budget). Observatory itself has no separate on-disk log — it's a pure read of the sources already listed (confirmed by reading `app/memory/reliability_metrics.py::compute_observatory()` directly).

## Part 2 — Clusters (the 21 named patterns, as the project's own taxonomy already defines them)

The user's prompt suggested example cluster names (AttributeError, ImportError, ModuleNotFoundError, Pydantic mismatch, SQLAlchemy mismatch, Route mismatch, JWT/Auth, Frontend build, Type mismatch, Dependency, Runtime startup, Deployment, Unknown). Cross-checked against the codebase's own taxonomy: **11 of those 13 example names map directly to existing pattern keys or stages; 2 do not appear as distinct clusters in the evidence** — noted rather than invented:

- **JWT/Auth as a distinct cluster: not found.** No pattern key named anything Auth/JWT-specific exists in `patterns.json`. The closest real evidence is the bundle-level finding (Part 3 below) that 9 of 14 `JourneyCRUDFailure` bundles are actually a missing `/auth/register` route — an auth-shaped failure, but the taxonomy currently buckets it under the generic integration-stage `JourneyCRUDFailure` label, not a dedicated Auth cluster. This is itself a finding: **auth failures are undercounted as a distinct, trackable category** because the classifier doesn't distinguish them.
- **Dependency as a distinct cluster: not found.** No pattern key or dominant_errors string in the 87-line log matches a dependency-resolution failure shape (e.g. pip/npm install failure). Either this genuinely doesn't happen, or it happens but isn't captured by any of the sources checked this cycle. Reported as **Unknown / no evidence found**, not "doesn't happen."
- **Deployment**: exists as a stage (`DeployFailure`, `HealthCheckFailure` pattern keys) but **zero occurrences of either in patterns.json** — `failure_report.py`'s own dashboard output literally printed `Deployment (no data)` this cycle. Consistent with `deploy_rate: null` in `compute_observatory()`'s live output (canaries run with `--no-deploy` by convention, per this project's CLAUDE.md).

The 21 real clusters, by volume (from `patterns.json`, all 440-run totals):

| Cluster | Count | % of 194 classified instances | Stage |
|---|---|---|---|
| MissingEndpoint | 48 | 24.7% | generation |
| JourneyCRUDFailure | 30 | 15.5% | integration |
| AttributeError | 18 | 9.3% | runtime |
| ImportError | 13 | 6.7% | runtime |
| ConfigAttributeError | 13 | 6.7% | runtime |
| SQLAlchemyError | 11 | 5.7% | runtime |
| RouterExportMismatch | 9 | 4.6% | build |
| ModuleNotFoundError | 9 | 4.6% | runtime |
| FrontendBuildError | 7 | 3.6% | build |
| NoReferencedTableError | 6 | 3.1% | runtime |
| SyntaxError | 6 | 3.1% | build |
| PydanticSerializationError | 5 | 2.6% | runtime |
| NotNullViolationError | 4 | 2.1% | runtime |
| ValidationError | 3 | 1.5% | runtime |
| RelationshipModelNotImported | 3 | 1.5% | runtime |
| ResponseValidationError | 2 | 1.0% | runtime |
| TimestampNotNullError, MonolithicSchemaError, FastAPIError, RelationshipMissingError, UserIdNotInjectedError, InvalidDependsType, ModelFieldMismatchError | 1 each | 0.5% each | mixed |

(194 = the taxonomy's own classified-instance count from `failure_report.py`'s live output; 440 is `patterns.json`'s `total_runs` counter, a different denominator — the taxonomy classifies *validation-error and runtime-error signals*, not every run 1:1, so these numbers are not meant to sum to each other. Reported as-is from the source, not reconciled into a single denominator that isn't actually in the data.)

## Part 3 — Per-cluster detail (first/last occurrence, frequency, affected apps, repair attempts/success/remaining)

Full first_seen/last_seen dates for every cluster are in the table above's source (`patterns.json`) and reproduced with app names in `docs/RUNTIME_KNOWLEDGE_BASE.md`'s per-cluster entries (Part 7) to avoid duplicating the same table twice in this document. Highlights that don't fit a simple table:

- **JourneyCRUDFailure decomposes into (at least) 4 distinct root causes**, evidenced by the 14 forensic bundles (all 2026-07-11, the richest and most recent evidence available):
  - **Group B — 9/14 bundles (64%)**: `POST /auth/register` returns 404 (route missing/unreachable). Recurring across 3 different commits (e7b1878, def583f, f1b636d) and both `auto` and `openrouter` providers, spanning ~2 hours on 2026-07-11, never resolved within that window. **This is the single largest, most concrete, most recent piece of evidence in the entire dataset.**
  - **Group D — 3/14 bundles (21%)**: `PUT /products/{id}` returns 405 (edit endpoint not implemented), same shape across 3 providers on commit f1b636d, project inventory_manager.
  - **Group A — 1/14 (7%)**: `POST` to create an entity returns 400, `"Priority ID does not exist"` — a foreign-key reference in the seeded/test payload pointing to a nonexistent row (a seeding-order or seed-data-consistency issue, not a missing route).
  - **Group C — 1/14 (7%)**: 405 on a request that turns out to target `/stats/summary` instead of the real entity-create endpoint — looks like a journey-runner route-targeting bug, not a generation defect.
  - **Repairability**: zero of the 14 bundles show evidence of a subsequent successful repair within the same run (the one bundle traceable to a `generation_log.jsonl` entry, FR-000014, has `succeeded: false` after `fix_count: 4`).

- **Repair attempts / success, project-wide (from `generation_log.jsonl`'s 87 real-telemetry runs)**: fix_count and eventual success correlate strongly and cleanly —

  | fix_count | runs | succeeded | success rate |
  |---|---|---|---|
  | 0 | 22 | 22 | 100% |
  | 1 | 4 | 4 | 100% |
  | 2 | 33 | 1 | 3% |
  | 3 | 14 | 0 | 0% |
  | 4 | 8 | 2 | 25% |
  | 5 | 6 | 0 | 0% |

  **Once a run needs a second repair attempt, it overwhelmingly does not recover.** This is the single clearest quantitative signal in the whole dataset and directly informs Part 8's ROI ranking: fixes that prevent a SECOND repair attempt from ever being needed (i.e., first-attempt or generation-time fixes) are worth categorically more than improvements to the repair loop's 3rd/4th/5th attempt, which the data shows essentially never pays off.

- **Deterministic prevention (from the same 87 runs)**: only 19/87 runs (22%) had ANY deterministic patcher fire at all. When one did fire, `stage.contract_conformance` (120 hits), `_inline_content_patches`/`stage.static` (26 each), `patch_database_py`/`preflight.fix_database_py` (19 each), and `preflight.fix_model_schema_notnull_gap`/`preflight.fix_missing_env` (18 each) dominate. Could **not** establish the requested patcher-to-error-class correlation for several named pairs (e.g. `_patch_strip_relationships` vs. `RelationshipMissingError`) — the relationship-family patchers show 0 fires in literally every one of the 87 runs, and `RelationshipMissingError`/`RelationshipModelNotImported` never appear in this log's `dominant_errors` at all (they only exist in the older, pre-07-06 `patterns.json` data). Reported as **Unknown / not enough overlapping data**, not inferred either way.

- **Affected apps**: `patterns.json`'s stored examples name specific projects per pattern (see Part 7's per-cluster tables for the full list); at the canary level (the controlled, repeated 3-4 app sample), **`todo` has never once shown `crud_ok: true`** across all 16 canary runs since crud measurement began (Experiment 020, 2026-07-06) — the single most persistent unresolved metric in the canary history. `crm` has the best `crud_ok` record (true in 2 of its measured runs, e.g. `exp056-baseline-r1` and `exp062-cross-app`).

## Part 4 — Category determination per cluster

| Cluster | Category | Evidence |
|---|---|---|
| **MissingEndpoint** | Architecture-issue / generation-bug, with a confirmed repair-gap | Endpoint promised in the architecture plan but never generated (two independent static validators, `validate_endpoints()` and `validate_frontend_api_calls()` in `app/services/endpoint_validator.py`, both confirm this at generation time). Repaired only via a per-file LLM call (`app/services/missing_file_service.py::generate_missing_file()`) — **no deterministic patcher exists for this pattern**, despite it being the single largest cluster by volume (48 instances). |
| **JourneyCRUDFailure** | Runtime-gap (inherently undetectable statically) compounded by a confirmed total repair-gap | `app/runtime/user_journey_runner.py` is the only detection mechanism (a live HTTP walk) — no static pre-check is possible for this class by nature. Grepped `app/repair/orchestrator.py` and `app/repair/preflight.py` for a dedicated journey-failure patcher: **none found** — it falls through to the generic, undifferentiated LLM repair loop. But the bundle deep-dive (Part 3) shows the *dominant* sub-cause (64% of recent evidence) is actually a **generation-bug wearing a runtime-detected symptom** — a route that was simply never generated, which is a fundamentally different (and more fixable) problem than "the live app misbehaves." |
| **AttributeError** | Mixed — partial validator-gap (expected; many instances are dynamically-typed and statically undetectable in general) plus an already-substantial repair investment (3+ dedicated patchers: `_patch_attr_access_mismatches`, `_patch_unsafe_model_hasattr_filter`, `_patch_ownership_fk_attribute_drift`) that has not eliminated the pattern (`last_seen` is still the dataset's most recent date). Most likely explanation supported by the evidence: this is a long tail of many distinct root causes sharing one exception type, not one fixable bug — diminishing returns on adding yet another narrow patcher. |
| **ImportError, ModuleNotFoundError** | Validator-gap | No dedicated static import-resolution check found in any `app/services/*_validator.py` file. Narrow deterministic patchers exist for specific known sub-cases (`_patch_missing_pydantic_imports`, `fix_missing_init`) but general import failures fall to the generic LLM loop. |
| **ConfigAttributeError** | Repair-gap CLOSED — a genuine solved-category candidate | `preflight.py::_fix_config_missing_attrs` (priority 14) is dedicated, documented (its own docstring cites two independently-discovered root causes it was extended to cover), and confirmed live via a 2026-07-06 canary note inside the code itself. `last_seen` in `patterns.json` is 2026-07-07 — one day after that fix shipped. Plausibly solved; **Unknown whether that one-day gap reflects stale pre-fix data still in the window or a residual edge case**, not resolved by this cycle's evidence. |
| **SQLAlchemyError, NoReferencedTableError, RelationshipModelNotImported** | Best-covered — durably fixed | Both dedicated static validators (`validate_database()`, `validate_orm_usage()`) and 4 dedicated, confirmed-firing patchers exist. `last_seen` dates are the *stalest* in the whole taxonomy (2026-06-27, 2026-06-22, 2026-06-22 respectively) — the strongest evidence of a durable fix anywhere in this dataset. |
| **RouterExportMismatch, FrontendBuildError** | Repair-heavy, apparently working | 3 dedicated patchers each; low, non-recent volume relative to the investment made. |
| **SyntaxError** | Well-covered, confirmed this session | `ast.parse()` in `_is_safe_to_write()` (both writer services, directly verified in this session's own Exp066 work, not inference) is the detection mechanism; inline auto-repair attempts exist. The specific "querystring-used-as-filename" sub-variant recorded in `patterns.json`'s examples was already root-caused and fixed in this project's prior-experiment history — the pattern's `last_seen: 2026-07-11` most likely reflects a *different*, not-yet-identified syntax-error shape recurring, not a regression of the already-fixed one. **Unknown which**, without a fresh generation to inspect. |
| **PydanticSerializationError** | Repair-heavy (4+ patchers), detection-gap | No dedicated static check found; still shows a recent `last_seen` (2026-07-11) despite the repair investment. |
| **NotNullViolationError** | Repair confirmed, historically significant | `preflight.py::_fix_model_schema_notnull_gap` (priority 24) is this project's own Experiment 012 fix. Detection and repair appear combined in the same function (preflight-style; not separately verified this cycle). |
| **ValidationError** | Unknown — possible taxonomy overlap, not confirmed | `_classify_validation_error()`'s substring checks for `"validationerror"` and `"responsevalidationerror"` are ordered so the more specific one is checked first, which should prevent misclassification — but this was not traced through an actual raw error string this cycle. Flagged for a future experiment's disambiguation, not asserted as a bug. |
| **Long tail (7 patterns, count=1 each)** | Unknown / not traced | Explicitly out of scope this cycle per the prioritization decision (time budget spent on the 15 patterns with count≥3, which cover 91% of classified instances). Their `_PATTERN_RULES` entries describe an intended fix in prose; whether corresponding code exists was not verified for these 7. |
| **NEW (Experiment 072): patcher scope-confusion** | Not yet in `patterns.json`'s 21-pattern taxonomy — found via live validation, not offline analysis | `_patch_attr_access_mismatches()` (`deterministic_patcher.py`) detects a field mismatch per-class but applies its fix via a file-wide blanket `re.sub()`, with no verification that the specific attribute access being rewritten belongs to an instance of the mismatched class. Confirmed live: independently corrupted a correctly-injected `auth_routes.py` in 2 of 4 apps in one canary run (`docs/EXP072_VALIDATION.md`), reproducing the exact Exp063 bug shape via a write path (`deterministic_patcher.py`'s direct `.write_text()`) Exp064's semantic guard does not cover. Root-caused, not fixed, per Experiment 072's own rules — a clear Exp073 candidate. **RESOLVED (Experiment 073, confirmed live Experiment 074)**: rewritten to be AST-scoped; a fresh 2026-07-12 canary reproduced the exact ambiguous-attribute-name shape live (`blog_cms`'s `post.description`/`post_in.description` collision) and confirmed only the genuinely mismatched object was rewritten — see `docs/EXP074_VALIDATION.md`. |
| **NEW (Experiment 074): NOT-NULL-on-PUT (update-path variant)** | Not yet in `patterns.json`'s 21-pattern taxonomy — found via live validation | A `PUT`/replace-update handler unconditionally writes every model column from the request body; when a `nullable=False` column is omitted from the payload (not every client resends every field on update), the write sets it to `NULL` instead of preserving the existing value, raising the same `IntegrityError` shape as `NotNullViolationError` above but via a different code path that entry's Exp012/013 fix doesn't cover (that fix is explicitly create-path only). Confirmed live in `inventory`'s `PUT /products/{id}` this cycle (`docs/EXP074_VALIDATION.md` §3). Self-resolved via the generic LLM repair loop this run, not by any deterministic patcher — root-caused, not fixed, flagged as Exp075's recommended (smaller-scope) target. **RESOLVED (Experiment 075, confirmed live Experiment 076)**: new `preflight.py::_fix_update_notnull_field_loss` (priority 27) guards the unconditional copy; live-confirmed firing correctly on real generated code (`inventory`, 2 files, 4 fields), zero false positives on already-correct code, zero model/CREATE-path side effects — see `docs/EXP076_LIVE_VALIDATION.md`. |
| **NEW (Experiment 076): wrong schema class bound to PUT parameter** | Not yet in `patterns.json`'s 21-pattern taxonomy — found via live validation, unrelated to Exp075/076's own target | `transaction_routes.py`'s `PUT` handler was generated as `update_transaction(transaction_in: TransactionCreate, ...)` instead of `TransactionUpdate` — since `TransactionCreate.quantity`/`unit_price` are non-Optional, any partial-update request omitting them gets a Pydantic-level 422 before the handler body (and Exp075's own guard) ever runs. A near-identical shape (required-field 422 on `PUT /products/1`) briefly appeared in `product_routes.py` in the same run before an unrelated repair pass rewrote that handler. Self-resolved via the LLM repair loop both times this cycle; root-caused, not fixed, per Exp076's own "no implementation changes unless a new root cause" rule — see `docs/EXP076_LIVE_VALIDATION.md` §5. |
