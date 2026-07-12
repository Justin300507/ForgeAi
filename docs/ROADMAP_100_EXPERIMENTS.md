# ForgeAI: Next 100 Experiments (Experiment 069, Part 14)

2026-07-12. Ordered exactly as recommended for execution — dependency-aware,
not just priority-sorted. Every item is grounded in a specific finding
from this experiment (Parts 1-9), Experiment 068's Runtime Roadmap, or
Experiment 065's Engineering Backlog — not invented. Where an item
overlaps a prior list, it's marked "(carries forward X)" rather than
silently duplicated. Difficulty/Risk: Low/Medium/High. API Cost:
$0 / Cerebras / Multi-provider. ROI: Low/Medium/High/Critical.

## Phase 0 — Critical security (do first, before anything commercial-facing)

| # | Title | Description | Deps | Difficulty | Risk | API Cost | Est. Time | ROI | Category | Success Criteria |
|---|---|---|---|---|---|---|---|---|---|---|
| 070 | Fix hardcoded SECRET_KEY default | Remove the insecure fallback in `app/dependencies/auth.py:13`; fail loudly at startup if unset in a production-like environment | None | Low | Low | $0 | 1-2h | Critical | Security | App refuses to boot with the default key in a non-dev environment; existing tests still pass |
| 071 | Harden `project_name` against path traversal | Apply `resolve_safe_path()` (Exp066 infra) to `project_name` before it's joined into `base_dir` in `file_writer_service.py:513-523` | None | Low | Low | $0 | 1-2h | High | Security | Malicious project-name strings rejected; existing generation flow unaffected (regression test) |
| 072 | Add rate limiting to `/login`, `/register`, `/project/v15` | Standard token-bucket middleware on the 3 highest-risk endpoints found in `docs/SECURITY_REVIEW_V2.md` | None | Low-Medium | Low | $0 | 2-4h | High | Security | Documented rate-limit response on excess requests; normal usage unaffected |
| 073 | Fix CORS misconfiguration | Replace `allow_origins=["*"]` + `allow_credentials=True` with an explicit allowed-origins list | None | Low | Low | $0 | 1h | Medium | Security | CORS preflight rejects unlisted origins; frontend still works |
| 074 | Add ownership check to `/api/download/{job_id}` | Tie the download to the requesting user's own jobs, not just a UUID | None | Low | Low | $0 | 1-2h | Medium | Security | Download 403s for a non-owning authenticated user |

## Phase 1 — The highest-ROI reliability fix (carries forward Exp068's own #1 recommendation)

| # | Title | Description | Deps | Difficulty | Risk | API Cost | Est. Time | ROI | Category | Success Criteria |
|---|---|---|---|---|---|---|---|---|---|---|
| 075 | Split "Auth" into its own taxonomy category | Add an Auth-specific pattern key to `failure_memory.py`'s classifier, separate from generic `JourneyCRUDFailure` (Exp068 Roadmap #8) | None | Low | Low | $0 | 2-3h | High | Observability | A missing-auth-route failure is now separately trackable before/after the fix below |
| 076 | Deterministic auth-route completeness check | Generation/preflight-time check enforcing the standard auth routes exist, reusing the known-good static template (Exp068 Roadmap #1 — the single highest-ROI item found across both this experiment and Exp068) | 075 | Medium | Medium | $0 (deterministic, no LLM call) | 1-2 days | Critical | Reliability | `todo`'s `crud_ok` shows `True` for the first time ever in a canary run |
| 077 | Deterministic edit-endpoint (PUT/PATCH) completeness check | Same mechanism as 076, targeting the 2nd-largest bundle group (21%, Exp068) | 076 | Medium | Medium | $0 | 1-2 days | High | Reliability | Edit-endpoint 405s eliminated from a targeted canary re-run |
| 078 | Give MissingEndpoint a deterministic repair path | Detection already solid (2 validators); currently 100% LLM-cost with no fallback (Exp068 Roadmap #3, this project's own largest cluster at 48 instances) | None | High | Medium | Cerebras (template-driven, not freeform) | 2-3 days | Critical | Reliability | A measurable fraction of MissingEndpoint repairs succeed without an LLM call |
| 079 | Investigate why repair attempts 2+ almost never succeed | Structural investigation of the repair loop itself (Exp068's cleanest quantitative finding: 100%→3%→0%→25%→0% by fix_count) | None | Medium | Low | $0 | 1 day | Critical | Repair | A documented root cause and a recommendation (cap the loop, or fix why later attempts regress) |
| 080 | Cap the repair loop at 2 attempts by default | Direct action on 079's finding if it confirms the loop should be capped | 079 | Low | Medium (verify no regression on the rare attempt-4 successes) | $0 | 4-6h | High | Repair | Average repair cost drops with no measurable drop in eventual success rate |
| 081 | Deterministic seed/test-data FK-consistency check | The 1/14-bundle FK-reference issue (Exp068 Roadmap #5) | None | Low | Low | $0 | 4-6h | Medium | Reliability | Seed-data FK-reference errors eliminated from a targeted re-run |
| 082 | Fix the JourneyCRUDFailure runner-targeting bug | ForgeAI's own test harness hits the wrong endpoint in 1/14 bundles (Exp068 Roadmap #17) — improves telemetry accuracy, not user-facing apps | None | Low | Low | $0 | 2-3h | Medium | Observability | The runner-targeting bundle group no longer recurs |
| 083 | Wire forensic bundles back into generation_log.jsonl | Only 1 of 87 log entries currently references a bundle (Exp068 Roadmap #9, also this cycle's own Observability finding) | None | Low-Medium | Low | $0 | 1 day | High | Observability | Every new failure produces exactly one queryable record, not two disconnected ones |
| 084 | Re-canary and confirm 076-078's combined effect | A full 3-4 app canary run measuring `first_try_success_rate` before/after Phase 1 | 076, 077, 078 | Low | Low | Multi-provider (canary) | 2-4h | Critical | Measurement | Documented before/after delta on the North-Star metric |

## Phase 2 — Taxonomy and observability consolidation

| # | Title | Description | Deps | Difficulty | Risk | API Cost | Est. Time | ROI | Category | Success Criteria |
|---|---|---|---|---|---|---|---|---|---|---|
| 085 | Reconcile `strategy_outcomes.json` vs. `patterns.json` taxonomies | Two incompatible granularities never reconciled (`docs/TECH_DEBT_MASTER.md` #8) | None | Medium | Low | $0 | 1 day | Medium | Observability | One documented mapping between the two, or a decision to retire one |
| 086 | Resolve ValidationError/ResponseValidationError/PydanticSerializationError taxonomy overlap | Trace one real raw error string through `_classify_validation_error()` (Exp068 Roadmap #7) | None | Low | Low | $0 | 3-4h | Medium | Observability | A documented answer: 1, 2, or 3 genuinely distinct problems |
| 087 | Remove 2 confirmed dead validator files | `import_validator.py`, `symbol_validator.py`, 147 lines, zero live callers (`docs/VALIDATOR_INTELLIGENCE.md`) | None | Low | Low | $0 | 30min | Low | Code quality | Files removed, full test suite still passes |
| 088 | Decide the fate of `RepairRegistry` | Designed, has a test file, never deployed (`docs/REPAIR_INTELLIGENCE.md`) | None | Low | Low | $0 | 2-3h (decision + doc) | Low | Code quality | Either deployed or the test file/design doc is retired with a documented reason |
| 089 | AttributeError long-tail triage | Bundle-decomposition method (like Exp068's JourneyCRUDFailure analysis) applied to this 18-instance cluster (Exp068 Roadmap #6) | None | Medium | Low | $0 | 1 day | Medium | Reliability | A documented answer: 1-2 dominant sub-shapes, or genuinely diffuse |
| 090 | Confirm or rule out a Dependency-failure category | Zero evidence found in existing logs; check the pipeline's own pip/npm install steps directly (Exp068 Roadmap #16) | None | Low | Low | $0 | 3-4h | Low | Observability | A documented answer, not an assumption |
| 091 | Re-verify SyntaxError's most recent recurrence | Is the 2026-07-11 instance the already-fixed querystring bug, or new? (Exp068 Roadmap #13) | None | Low | Low | Cerebras (1 fresh generation) | 2-3h | Medium | Reliability | A documented answer from inspecting a fresh generation |
| 092 | Re-verify ConfigAttributeError's apparent closure | Same cheap-check logic (Exp068 Roadmap #14) | None | Low | Low | Cerebras (1 fresh generation) | 2-3h | Low | Reliability | Confirmed durably resolved, or a residual edge case documented |
| 093 | Formally retire durably-fixed clusters in the taxonomy | `NoReferencedTableError`, `RelationshipModelNotImported` — 15+ days stale, zero recurrence (Exp068 Roadmap #19) | None | Low | Low | $0 | 1-2h | Low | Observability | A `retired`/`low-priority` flag so future dashboards don't re-surface them |
| 094 | Clarify RouterExportMismatch's detection-vs-repair boundary | Unknown whether they're combined in one function or separate (Exp068 Roadmap #15) | None | Low | Low | $0 | 2-3h | Low | Observability | A documented answer |

## Phase 3 — Validator and repair coverage

| # | Title | Description | Deps | Difficulty | Risk | API Cost | Est. Time | ROI | Category | Success Criteria |
|---|---|---|---|---|---|---|---|---|---|---|
| 095 | Write dedicated tests for `endpoint_validator.py` | Zero coverage today for the detector of the project's largest failure cluster (`docs/VALIDATOR_INTELLIGENCE.md`) | None | Medium | Low | $0 | 1 day | Critical | Testing | Full coverage of `validate_endpoints()`/`validate_frontend_api_calls()`/`validate_orphan_routes()` |
| 096 | Write dedicated tests for `PATCH_FILE`/`SWITCH_MODEL` strategy dispatch | Currently only narrower behavioral slices are tested (`docs/REPAIR_INTELLIGENCE.md`) | None | Medium | Low | $0 | 1 day | Medium | Testing | Both strategies have direct, named test coverage |
| 097 | Surface (not swallow) deterministic-patcher crashes | The bare `except Exception` at `orchestrator.py:1106` (`docs/TECH_DEBT_MASTER.md` #9) | None | Low | Medium (verify no downstream code relies on silent swallowing) | $0 | 4-6h | High | Repair | A patcher crash now produces a visible diagnostic, not just a print |
| 098 | Add module-level docstrings to all 14 validator files | Zero exist today (`docs/VALIDATOR_INTELLIGENCE.md`) | None | Low | Low | $0 | 1 day | Low | Documentation | Every validator file has a real purpose/inputs/outputs docstring |
| 099 | Remove the leftover LLM-authoring artifact comment in `session_validator.py` | A small hygiene finding, worth checking for siblings elsewhere | None | Low | Low | $0 | 1-2h | Low | Code quality | Comment fixed; a grep sweep for similar artifacts across the codebase |
| 100 | ImportError general static check | Currently zero dedicated detection for this 13-instance cluster (Exp068 Roadmap #10) | None | Medium | Low | $0 | 1 day | Medium | Reliability | A narrow AST-based "does every imported name exist" check catches a measurable fraction |
| 101 | ModuleNotFoundError general static check | Same rationale, 9-instance cluster (Exp068 Roadmap #11) | None | Medium | Low | $0 | 1 day | Medium | Reliability | Same success criteria as 100 |
| 102 | PydanticSerializationError static pre-check | 4+ repair patchers exist with no detection (Exp068 Roadmap #12) | None | Medium | Low | $0 | 1 day | Medium | Reliability | A detection mechanism reveals whether the existing repair investment is actually resolving the issue |
| 103 | Fix `_regenerate_architecture`'s first-group-only strategy selection | A structural sharp edge found in `docs/REPAIR_INTELLIGENCE.md` — confirm if it's an active bug before fixing | None | Low | Low | $0 | 3-4h (investigation) | Low | Repair | A documented answer; a fix only if confirmed active |
| 104 | Harden `_apply_fix_group()`'s 4 remaining unhardened write call sites | The last unhardened write paths from Experiments 066/067's own scope decision (`docs/WRITE_VALIDATION_MATRIX.md`) | None | Medium | Low | $0 | 1 day | High | Security/Write pipeline | Same `resolve_safe_path()`/`atomic_write_text()` pattern applied to all 4 sites, zero regressions |

## Phase 4 — Performance

| # | Title | Description | Deps | Difficulty | Risk | API Cost | Est. Time | ROI | Category | Success Criteria |
|---|---|---|---|---|---|---|---|---|---|---|
| 105 | Shared AST-parse cache across the 12 validators | Up to N×12 redundant parses per verification pass (`docs/PERFORMANCE_REVIEW_V2.md` Finding #1) | None | Medium | Low | $0 | 1-2 days | High | Performance | Measured reduction in `ast.parse()` call count per verification pass, identical validator outputs |
| 106 | Reduce `validate_project()`'s ~20 redundant `os.walk()` calls | Carries forward Exp059/065's still-valid finding | None | Medium | Low | $0 | 1 day | Medium | Performance | Measured reduction in filesystem walk count, identical outputs |
| 107 | Trace `llm_cache/`'s (5,901-file) memory access pattern | Not verified this cycle whether it's ever loaded as a whole (`docs/PERFORMANCE_REVIEW_V2.md`) | None | Low | Low | $0 | 3-4h | Low | Performance | A documented answer |
| 108 | Full per-pattern ReDoS review | Only a narrow heuristic sweep was done this cycle | None | Medium | Low | $0 | 1 day | Low | Security/Performance | Every regex in the validator/patcher layer reviewed for backtracking risk |
| 109 | Trace repeated large-JSON-file reads | Not exhaustively checked this cycle whether telemetry files get re-parsed within a request (`docs/PERFORMANCE_REVIEW_V2.md`) | None | Low | Low | $0 | 4-6h | Low | Performance | A documented answer, and a caching fix if a real hotspot is found |

## Phase 5 — Scalability

| # | Title | Description | Deps | Difficulty | Risk | API Cost | Est. Time | ROI | Category | Success Criteria |
|---|---|---|---|---|---|---|---|---|---|---|
| 110 | Fix `cost_tracker.py`'s module-global state under concurrent requests | Carries forward Exp065's still-valid finding — a real cross-request contamination risk on the sync `/project/v15` route | None | Medium | Medium | $0 | 1 day | High | Scalability | Concurrent-request cost tracking isolated per-request, verified via a concurrency test |
| 111 | Reconcile `FORGE_DEPLOY_THRESHOLD`'s duplicate definition | `core/context.py` vs. `scoring/engine.py` (`docs/TECH_DEBT_MASTER.md` #19) | None | Low | Low | $0 | 2-3h | Medium | Code quality | One source of truth, both call sites reference it |
| 112 | Add token revocation/blacklist for JWT auth | No mechanism today (`docs/SECURITY_REVIEW_V2.md` Finding #6) | None | Medium | Low | $0 | 1-2 days | Low | Security | A revoked token is rejected before its natural expiry |
| 113 | Move the sync `/project/v15` path toward the async job-queue's process-isolation model | The queue path is already architecturally sounder; make it the default | 110 | High | Medium | $0 | 3-5 days | High | Scalability | `/project/v15` requests no longer share module-global state |
| 114 | Load-test the generation pipeline under concurrent requests | No evidence found this cycle of any prior load test | 072, 110 | Medium | Low | Multi-provider (real load test) | 1 day | Medium | Scalability | A documented concurrent-request capacity number |

## Phase 6 — Contract-first migration (V2's guiding principle, incremental path)

| # | Title | Description | Deps | Difficulty | Risk | API Cost | Est. Time | ROI | Category | Success Criteria |
|---|---|---|---|---|---|---|---|---|---|---|
| 115 | Re-evaluate AppContract adoption with fresh, controlled evidence | The prior evaluation was inconclusive; conditions have changed significantly since (multiple reliability fixes shipped) | 084 | Medium | Low | Multi-provider (controlled A/B) | 1-2 days | High | Architecture | A fresh, non-confounded verdict on AppContract's measurable benefit |
| 116 | Make AppContract's auth-route requirements the source of truth for 076 | Tie Phase 1's auth-route fix directly to the contract IR rather than a standalone check | 076, 115 | Medium | Low | $0 | 1 day | High | Architecture | Auth-route completeness is now contract-derived, not a separate mechanism |
| 117 | Expand AppContract to cover CRUD-endpoint-per-entity requirements | Extends 116's pattern to the broader MissingEndpoint cluster | 078, 116 | High | Medium | Cerebras | 2-3 days | High | Architecture | A measurable fraction of MissingEndpoint instances become contract-preventable, not just contract-detectable |
| 118 | Migrate 3 more validators onto contract-conformance checking | Incremental adoption, not a big-bang rewrite | 117 | Medium | Low | $0 | 2 days | Medium | Architecture | 3 validators now check against the contract, zero regressions |
| 119 | Deprecate 3 of the 10 dead historical generation endpoints | `/v6` through `/v14` remain registered; start retiring the oldest, least-used ones | None | Low | Medium (confirm zero live usage first) | $0 | 1 day | Low | Code quality | 3 fewer legacy endpoints, confirmed zero production traffic first |
| 120 | Consolidate the two parallel benchmark systems | `run_benchmark.py` vs. `run_forgebench.py` (`docs/TECH_DEBT_MASTER.md` #10) | None | High | Medium | $0 | 2-3 days | Medium | Code quality | One benchmark entry point, both prior systems' capabilities preserved |
| 121 | Reconcile the two frontend API-client files | `api.js` vs. `lib/api.js` — confirm which is live, remove the other | None | Low | Low | $0 | 3-4h | Low | Code quality | One API client, frontend still works |
| 122 | Begin `main.py` route-group modularization | Move one route group (e.g. deploy actions) to `include_router()`, proving the pattern before a full migration | None | Medium | Low | $0 | 1 day | Medium | Code quality | 1 route group modularized, zero behavior change |
| 123 | Modularize 3 more route groups | Continue 122's pattern | 122 | Medium | Low | $0 | 2 days | Medium | Code quality | 4 total route groups modularized |
| 124 | Complete `main.py` modularization | Every route group through `include_router()` | 123 | High | Medium | $0 | 3-4 days | Medium | Code quality | `main.py` under 300 lines, all routes still functional |

## Phase 7 — CLI and developer experience

| # | Title | Description | Deps | Difficulty | Risk | API Cost | Est. Time | ROI | Category | Success Criteria |
|---|---|---|---|---|---|---|---|---|---|---|
| 125 | Design a unified CLI entry point (`click` or `typer`) | Consolidate the ~8 standalone scripts under one command tree | None | Medium | Low | $0 | 1 day (design only) | Medium | DX | A documented CLI command-tree design |
| 126 | Migrate `run_canary.py` to the unified CLI | First migration, proves the pattern | 125 | Low | Low | $0 | 4-6h | Low | DX | `forgeai canary` works identically to the old script |
| 127 | Migrate 3 more scripts to the unified CLI | Continue 126's pattern | 126 | Medium | Low | $0 | 1-2 days | Low | DX | 4 total scripts migrated |
| 128 | Migrate the remaining scripts | Complete the CLI consolidation | 127 | Medium | Low | $0 | 2 days | Low | DX | All ~8 scripts under one CLI, old scripts removed or aliased |
| 129 | Add a repo-level README with an onboarding path | No confirmed onboarding doc found this cycle | None | Low | Low | $0 | 4-6h | Medium | Documentation | A new engineer can find "how do I run this" in under 5 minutes |
| 130 | Reorganize `backend/tests/` toward module-alignment | Currently topic-organized, making coverage gaps hard to find (`docs/TECH_DEBT_MASTER.md` #20) | None | High | Medium | $0 | 3-5 days | Medium | Testing | Test-directory structure mirrors `app/` structure, or a documented mapping exists |
| 131 | Add docstrings to the top 10 highest-fan-in modules | `ai_provider.py`, `json_cleaner.py`, `context.py`, etc. | None | Low | Low | $0 | 1 day | Low | Documentation | 10 modules documented |
| 132 | Audit and document every environment variable ForgeAI reads | No confirmed single source of truth found this cycle | None | Low | Low | $0 | 4-6h | Medium | Documentation | A complete, accurate env-var reference document |
| 133 | Add a CONTRIBUTING-style guide reflecting this project's own established discipline | This project's own experiment methodology (evidence-only, canary-verified, one-fix-per-cycle) is genuinely distinctive — codify it | None | Low | Low | $0 | 4-6h | Low | Documentation | A new contributor can read the project's own working norms in one place |
| 134 | Set up automated docstring-coverage tracking | Prevent the 0%-docstring-coverage finding from recurring silently | 098, 131 | Medium | Low | $0 | 1 day | Low | DX | A CI-visible docstring-coverage metric |

## Phase 8 — Testing discipline

| # | Title | Description | Deps | Difficulty | Risk | API Cost | Est. Time | ROI | Category | Success Criteria |
|---|---|---|---|---|---|---|---|---|---|---|
| 135 | Build a shared replay-test fixture library | Every "extract real file, run check, assert" test this project's own history has needed (5+ times per Exp065's own finding) re-solved the same problem from scratch | None | Medium | Low | $0 | 1-2 days | Medium | Testing | A reusable fixture library, at least 3 existing tests migrated to use it |
| 136 | Add a performance-budget regression test | No test exists asserting a call-count or timing budget anywhere (Exp065's own finding) | 105, 106 | Low | Low | $0 | 4-6h | Medium | Testing | A test fails if `ast.parse()`/`os.walk()` call counts regress upward |
| 137 | Property-based testing pilot for one validator | Zero property-based tests exist anywhere (Exp065's own finding) | None | Medium | Low | $0 | 1 day | Low | Testing | One validator has hypothesis-style property tests |
| 138 | Add integration tests spanning validator + repair + verification | Only one such cross-subsystem test exists today (Exp065's own finding) | None | Medium | Low | $0 | 1-2 days | Medium | Testing | 2-3 new integration tests covering realistic multi-subsystem flows |
| 139 | Full docstring-quality pass, not just coverage | Distinguish "has a docstring" from "the docstring is actually useful" | 098, 134 | Low | Low | $0 | 1 day | Low | Documentation | A qualitative review of the newly-added docstrings |

## Phase 9 — Deploy path reliability

| # | Title | Description | Deps | Difficulty | Risk | API Cost | Est. Time | ROI | Category | Success Criteria |
|---|---|---|---|---|---|---|---|---|---|---|
| 140 | Investigate why `deployment_success` is 0/51 in the variance report | The deploy-provider architecture is clean, but the numbers are zero — find out why | None | Medium | Low | Multi-provider (1-2 real deploy attempts) | 1 day | High | Deployment | A documented root cause |
| 141 | Fix the root cause found in 140 | Direct action on the investigation | 140 | Medium-High | Medium | Multi-provider | 1-3 days | High | Deployment | `deployment_success` measurably above 0% on a fresh canary run with `--deploy` |
| 142 | Add deploy-path canary coverage as a standing practice | Canaries currently run `--no-deploy` by convention — decide when/how often to include the deploy path | 141 | Low | Low | Multi-provider (periodic) | 4-6h (design) | Medium | Measurement | A documented cadence for deploy-inclusive canary runs |
| 143 | Add deployment-stage telemetry | Currently zero data (Exp068 Roadmap #20) | 141 | Low | Low | $0 | 1 day | Low | Observability | Deploy attempts/successes/failures tracked in the same telemetry system as generation |
| 144 | Post-deploy health-check hardening | Cross-reference `docker_validator.py`/`deployment_validator.py` for gaps | 141 | Medium | Low | $0 | 1 day | Medium | Deployment | A documented review of post-deploy validation coverage |

## Phase 10 — V2 subsystems (architecture work, incremental toward `docs/FORGEAI_V2.md`)

| # | Title | Description | Deps | Difficulty | Risk | API Cost | Est. Time | ROI | Category | Success Criteria |
|---|---|---|---|---|---|---|---|---|---|---|
| 145 | Design the unified validator/repair plugin interface | `docs/FORGEAI_V2.md` Part 13's subsystem #2 — design only | 118 | Medium | Low | $0 | 2 days | Medium | Architecture | A documented plugin interface spec |
| 146 | Migrate 2 validators to the new plugin interface (pilot) | Prove the design from 145 | 145 | Medium | Low | $0 | 1-2 days | Medium | Architecture | 2 validators working under the new interface, zero regressions |
| 147 | Migrate 2 patchers to the new plugin interface | Same pilot, repair side | 145 | Medium | Low | $0 | 1-2 days | Medium | Architecture | 2 patchers working under the new interface, zero regressions |
| 148 | Design the contract-checked-per-agent-output model | `docs/FORGEAI_V2.md` subsystem #3 — design only | 115 | Medium | Low | $0 | 1-2 days | Medium | Architecture | A documented design for mid-generation contract checking |
| 149 | Pilot mid-generation contract checking for one agent stage | Prove 148's design on the backend-generation stage only | 148 | High | Medium | Cerebras | 2-3 days | High | Architecture | Contract violations caught mid-generation, before the full verification pass |
| 150 | Wire `compute_observatory()` into the live pipeline as a feedback signal | `docs/FORGEAI_V2.md` subsystem #5 — currently read-only | 083 | High | Medium | $0 | 2-3 days | High | Observability | The pipeline can react to a live reliability-trend signal, not just report it |
| 151 | Design per-tenant rate limiting for a future multi-tenant deployment | `docs/FORGEAI_V2.md` subsystem #7 | 072 | Medium | Low | $0 | 1 day | Medium | Enterprise | A documented per-tenant rate-limit design |
| 152 | Design an audit-logging layer tied to the observability system | Same subsystem #7 | 150 | Medium | Low | $0 | 1-2 days | Medium | Enterprise | A documented audit-log design |
| 153 | Design a real secrets-management story | Direct response to 070's finding — a proper secrets vault/rotation design, not just fixing the one default | 070 | Medium | Low | $0 | 1 day | Medium | Enterprise | A documented secrets-management design |
| 154 | Re-run this experiment's own competitive analysis with live research (not offline) | This cycle's Part 12 was explicitly offline-only; a follow-up with real research would sharpen it | None | Low | Low | $0 (research, not generation) | 1 day | Low | Strategy | An updated competitive analysis citing live sources |

## Phase 11 — Long-tail cleanup and remaining clusters

| # | Title | Description | Deps | Difficulty | Risk | API Cost | Est. Time | ROI | Category | Success Criteria |
|---|---|---|---|---|---|---|---|---|---|---|
| 155 | Investigate the 7 remaining count=1 failure clusters | Not traced in Experiment 068 or 069 (`docs/RUNTIME_KNOWLEDGE_BASE.md` long tail) | None | Low | Low | $0 | 1 day | Low | Observability | Each cluster gets a documented detection/repair status |
| 156 | Full read of all 66 `deterministic_patcher.py` functions | This cycle sampled representative ones; a full read may find more issues like the param-order/attr-access bugs Exp052 found | None | High | Low | $0 | 2-3 days | Medium | Repair | A documented review of every function, with any found bugs filed as follow-ups |
| 157 | Trace `replay/` subsystem's actual callers | Flagged as a possible dead-code candidate, not confirmed (`docs/ARCHITECTURE_ATLAS.md`) | None | Low | Low | $0 | 3-4h | Low | Code quality | A documented answer: live and used, or dead and removable |
| 158 | Full temp-file audit beyond `atomic_write.py` | Not exhaustively checked this cycle (`docs/SECURITY_REVIEW_V2.md`) | None | Low | Low | $0 | 1 day | Low | Security | Every `tempfile` usage site reviewed for permissions/cleanup |
| 159 | Confirm `get_current_user()` gates every mutating endpoint | Not confirmed this cycle | None | Low | Low | $0 | 4-6h | Medium | Security | A documented audit of every mutating endpoint's auth gate |
| 160 | Full memory-profile pass on `llm_cache/` access patterns | Not exhaustively checked this cycle | 107 | Medium | Low | $0 | 1 day | Low | Performance | A documented memory-access-pattern analysis |
| 161 | Investigate the two-fork-conflicting-Exp059-text data anomaly | `docs/ENGINEERING_HISTORY.md`'s own flagged anomaly — did Exp059's entry get edited after Exp064 shipped, or is this a genuine duplication? | None | Low | Low | $0 | 2-3h | Low | Process | A documented resolution of the anomaly |
| 162 | Re-verify Exp061's flagged evidence gap | The regex-fallback validator path was never exercised live — still true? | None | Low | Low | Cerebras (targeted repro) | 3-4h | Low | Validation | A documented answer |
| 163 | Full competitive feature-parity gap analysis vs. one named competitor | Pick the single most architecturally comparable competitor from Part 12 and go deeper | 154 | Medium | Low | $0 | 1-2 days | Medium | Strategy | A detailed feature-by-feature comparison |
| 164 | Retrospective: measure this experiment's own recommendations' actual uptake | A future check-in on how many of experiments 070-163 actually got executed and what they found — the project's own established "measure, don't just recommend" discipline applied to this very roadmap | All prior | Low | Low | $0 | 1 day | High | Process | A documented uptake report, honest about what didn't get done and why |

## Phase 12 — Final polish (rounding out to 100)

| # | Title | Description | Deps | Difficulty | Risk | API Cost | Est. Time | ROI | Category | Success Criteria |
|---|---|---|---|---|---|---|---|---|---|---|
| 165 | Full read of all 23 `app/prompts/*.py` files | Not individually read in this experiment's Codebase Atlas pass (`docs/ARCHITECTURE_ATLAS.md`) — the highest-leverage, least-audited directory in the repo given every prompt shapes every generation | None | Medium | Low | $0 | 1-2 days | Medium | Generation | A documented review of every prompt file's structure and any found inconsistencies |
| 166 | Audit `app/design/`'s 8 files in depth | Not individually read this cycle; called from the write pipeline but its own internals unexamined | None | Medium | Low | $0 | 1 day | Low | Generation | A documented design-pipeline review |
| 167 | Audit `app/knowledge/`'s non-`lucide_icon_exports.py` files | Only the largest file was characterized this cycle; 5 other files unexamined | None | Low | Low | $0 | 4-6h | Low | Code quality | A documented review |
| 168 | Confirm password-strength validation exists (or add it) on `/register` | Flagged as absent this cycle, not deeply investigated (`docs/SECURITY_REVIEW_V2.md`) | None | Low | Low | $0 | 3-4h | Medium | Security | Weak passwords rejected at registration |
| 169 | Six-month retrospective: re-run this experiment's own methodology | A full re-audit using this same 15-part structure, to measure how much of the roadmap above actually shipped and moved the numbers — the project's own "measure, don't just build" discipline applied at the largest scale | 164 | High | Low | Minimal | 4-6 hours (per this experiment's own duration) | High | Process | A documented comparison against this experiment's own baseline scores |

## Ordering rationale

Phases 0-1 come first because they're the highest-severity,
highest-ROI, lowest-effort items with the least dependency on
anything else (per this document's own scoring). Phase 1 specifically
sequences the auth-route fix before broader MissingEndpoint work
because the auth-route sub-cause is both the single largest piece of
concrete evidence (Exp068's bundle analysis) and the cheapest to fix
(reuses existing infrastructure). Phases 2-3 (taxonomy/coverage
cleanup) are sequenced before Phase 4-5 (performance/scalability)
because measurement quality compounds — every later phase's "did this
help" question is answered more precisely once Phase 2's taxonomy
reconciliation lands. Phase 6 (contract-first migration) deliberately
comes after Phase 1 ships, so its own re-evaluation (item 115) isn't
confounded by Phase 1's changes the way several prior AppContract
evaluations were confounded by provider quota issues. Phases 7-9 are
lower-urgency, higher-effort structural work appropriately sequenced
after the reliability/security fixes that matter more for a near-term
commercial decision. Phase 10 (V2 subsystems) intentionally starts
only after its prerequisite Phase 1/6 work ships, since V2's design
(`docs/FORGEAI_V2.md`) is explicitly built on evidence from a
post-auth-fix, post-contract-re-evaluation state. Phase 11 is genuine
long-tail cleanup, correctly last.
