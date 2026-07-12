# ForgeAI Architecture Atlas (Experiment 069, Part 2)

2026-07-12. Every directory under `backend/app/` (22 confirmed via
direct listing, not assumed) plus `frontend/src/` and `backend/tests/`.
"Unknown" fields are genuinely unresolved this cycle, not omitted.

| Directory | Files | Purpose | Key files | Deps / Callers | Risk signal | Test coverage |
|---|---|---|---|---|---|---|
| `services/` | 83 | Largest cluster — generation team services, ~13 validators, writers, patchers | `deterministic_patcher.py` (6621 lines, largest file in repo), `v6_orchestrator.py` (1250 lines, highest-complexity function per Exp065), `database_patcher.py` (1314 lines) | Imports providers/core/utils; called by pipeline/repair | **HIGH** — single largest, most complex directory in the repo | `tests/reliability/` covers many services individually |
| `repair/` | 5 | Fix-loop orchestration | `orchestrator.py` (1173 lines), `preflight.py`, `grouper.py` | Imports services (incl. the reverse-layer call into `v6_orchestrator`), `retry/` | MEDIUM-HIGH — the reverse-layer dependency is itself a risk (Exp065 finding) | `tests/reliability/` — 2 new files added directly by Experiments 066-067 |
| `verification/` | 4 | Static+runtime scoring/validation orchestration | `engine.py` (1705 lines), `graph.py` (FailureGraph) | Imports services/validators, runtime, contract | **HIGH** — largest single orchestration function complexity (Exp065) | 33% docstring coverage (Exp065's own finding) |
| `runtime/` | 12 | Live smoke-test / journey / browser / deploy validation | `user_journey_runner.py` (166 complexity / 15 depth per Exp059 — the single worst raw complexity score in the codebase) | Called only from `verification/engine.py` | **HIGH** — worst raw complexity in the codebase, 25.6% docstring coverage (Exp065) | Not confirmed 1:1 — no `tests/runtime/` directory exists |
| `providers/` | 9 | LLM provider abstraction + fallback chain | `ai_provider.py` (28 real importers, highest fan-in in repo) | Imported by nearly every generation/repair/benchmark path | **HIGH consequence-of-change** (widest blast radius in the repo) | 44.4% docstring coverage (Exp065) |
| `core/` | 5 | Pipeline entry + shared context types | `pipeline.py` (V15Pipeline, L47), `context.py` (Diagnostic/GenerationContext, 22 real importers) | Imported everywhere | MEDIUM — high fan-in, `context.py` recently extended (Exp060) | Unknown |
| `contract/` | 4 | AppContract IR (priority-1 item in this project's own VNext report) | `models.py` (AppContract), `adapter.py` (from_architecture_plan), `validator.py` (check_contract_conformance) | Called from `verification` (`_run_contract_conformance_check`) | MEDIUM — partially-adopted, per this project's own `project_appcontract_eval_inconclusive` history | At least one confirmed test file in `tests/adr001_ext/` |
| `confidence/` | 2 | Deployment-confidence scoring from GenerationContext | `engine.py` | Called from verification pipeline | LOW file count; historically risky (a 2026-07-06 bug fix corrected every attribute name being wrong — Exp005) but currently fixed | Unknown |
| `scoring/` | 2 | Forge Score weighted computation | `engine.py::ScoringEngine` (L309) | `FORGE_DEPLOY_THRESHOLD` independently redefined here vs. `core/context.py` (confirmed duplication, Exp065 finding) | MEDIUM — confirmed silent-divergence risk | Unknown |
| `queue/` | 5 | Async job dispatch (V19) | `job_queue.py` (SQLite, documented Redis upgrade path), `dispatcher.py` (singleton `WorkerDispatcher`, confirmed correctly-scoped per Exp065), `worker.py` (standalone process), `api.py` (the one real `include_router()` in `main.py`) | Separate OS processes for isolation (safer than the sync `/project/v15` path per Exp065's state-management finding) | LOW-MEDIUM | Unknown |
| `retry/` | 3 | 5-level strategy escalation config | `manager.py::RetryManager/StrategyConfig`, `strategy_memory.py` | Used by `repair/orchestrator.py` | LOW file count | Unknown |
| `replay/` | 3 | Generation replay recorder/viewer (V18 feature) | `recorder.py`, `viewer.py` | Unknown callers, not traced this cycle | LOW — a possibly under-used subsystem worth a dead-code check | Unknown |
| `deployments/` | 7 | Deploy provider abstraction | `base_provider.py` (ABC, clean `DeploymentResult` dataclass), 4 provider impls + `render_config.py` | Called from `main.py`'s `/deploy/*` routes | LOW-MEDIUM — one of the better-designed subsystems structurally (see `docs/SYSTEM_DESIGN.md`) | Unknown |
| `memory/` | 6 | Telemetry aggregation | `reliability_metrics.py` (Observatory's entire data source — fully traced Experiments 068/069), `failure_memory.py` (Exp068 subject), `experiment_log.py` (parses `experiments.md` live) | Read by `/observatory`, `failure_report.py` | LOW-MEDIUM — pure aggregation, well-isolated | Unknown |
| `benchmark/` | 7 | ForgeBench suite support | `metrics.py`, `reporter.py`, `loader.py`, `kpi_dashboard.py`, `heatmap.py`, `history.py` | Two parallel entry scripts (`run_benchmark.py`, `run_forgebench.py`) both depend on this — a confirmed duplication risk | MEDIUM | Unknown |
| `design/` | 8 | Design Intelligence v2 (brief pipeline, component metadata, fingerprinting) | Not individually read this cycle | Called from `file_writer_service.py` (confirmed, `write_files()`'s idea-driven design-brief block) | Unknown | `tests/design_intelligence/` directory confirmed to exist |
| `knowledge/` | 6 | Static reference data + failure-pattern DB | `lucide_icon_exports.py` (3713 lines, 2nd-largest file in repo, confirmed static data not logic per Exp065) | Unknown | LOW — large but data, not logic | Unknown |
| `prompts/` | 23 | LLM prompt templates | Not individually read | **Known false-positive source for naive dependency grep** (Exp065's own methodology finding — embeds literal example-import strings) | N/A — prompt content | N/A |
| `models/` | 7 | ForgeAI's own SQLAlchemy/Pydantic models (User, GenerationJob, etc — distinct from generated-app templates) | `user.py`, `generation_job.py`, `project_models.py`, `architecture_models.py`, `backend_models.py`, `frontend_models.py`, `user_credentials.py` | Imported by `main.py`, `app/dependencies/auth.py`, `app/database.py` | LOW | Unknown |
| `dependencies/` | 1 | FastAPI dependency injection | `auth.py` (JWT/OAuth2 — see `docs/SYSTEM_DESIGN.md` §10) | Imported by every gated route | LOW file count, but **security-relevant** (hardcoded SECRET_KEY default) | Unknown |
| `routes/` | 1 | **Nearly unused** — only `architect.py`, despite `main.py` having ~45 inline routes | Not read | Confirms `main.py`'s monolithic-route-file finding | LOW-MEDIUM — organizational debt (one stray file in an otherwise-unused directory) | Unknown |
| `templates/` | 3 | Static scaffold templates for generated apps | Not read | Called from `file_writer_service.py` | LOW | N/A |
| `utils/` | 8 | Shared utilities | `cost_tracker.py` (state-management risk, Exp065 finding), `json_cleaner.py` (27 real importers, 2nd-highest fan-in in repo), `safe_path.py`/`atomic_write.py` (Experiment 066 additions) | Widely imported | MEDIUM — `json_cleaner`'s high fan-in makes it a consequence-of-change risk | `tests/reliability/test_exp066_write_pipeline_hardening.py` covers 2 of 8 files here |
| `browser/` | 1 (+tests) | Browser automation support | Not read | Unknown | LOW file count | Has its own `tests/` subdirectory (unusual — most directories rely on top-level `backend/tests/`) |

**Frontend** (`frontend/src/`): `App.jsx` (routing root), `AuthContext.jsx`
+ `hooks/useAuth.jsx` (auth state), a top-level `api.js` AND `lib/api.js`
(**possible duplication, not reconciled this cycle**), `pages/` (11
files), `components/` (5 files, mostly visual/cinematic), `lib/cinematic.js`
+ `lib/pipelineStages.js`. No dedicated frontend test directory found.

**Test directory** (`backend/tests/`, confirmed via listing):
`adr001_ext/`, `adr002/`, `deployment/`, `design_intelligence/`,
`reliability/` — 5 subdirectories, `reliability/` by far the largest.
**No subdirectory maps 1:1 to most `app/` directories** above (no
`tests/runtime/`, `tests/queue/`, `tests/deployments/`) — coverage is
topic-organized, not module-organized, which makes "does X have test
coverage" a genuinely fuzzy question for several modules, marked
Unknown above rather than guessed.

## Flagged, not deeply investigated this cycle

- The `main.py`-only `api.js` / `lib/api.js` possible duplication in
  the frontend.
- The two-parallel-benchmark-systems question (`run_benchmark.py` vs.
  `run_forgebench.py`).
- Whether `get_current_user()` gates every mutating endpoint or only
  some.
- The `replay/` subsystem's actual callers (a dead-code candidate,
  unconfirmed).
- The hardcoded insecure `SECRET_KEY` default in
  `app/dependencies/auth.py:13-14` — full detail in
  `docs/SECURITY_REVIEW_V2.md`.
