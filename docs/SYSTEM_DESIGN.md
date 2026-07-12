# ForgeAI System Design (Experiment 069, Part 1)

2026-07-12. Reverse-engineered directly from source, not from any
prior design document. Every box below is a real file:line reference,
confirmed via direct reads and `graphify` this cycle. Cross-references
`docs/ARCHITECTURE_REVIEW.md` (Exp065) and `docs/WRITE_PIPELINE.md`
(Exp066/067) where those already cover a subsystem in depth — not
re-derived here.

## 1. Generation pipeline

```
main.py:1209 POST /project/v15
  -> V15Pipeline (app/core/pipeline.py:47)
       1. Generation: app/services/v6_orchestrator.py::generate_project_v6()
          (V6 multi-agent team: product_manager_service -> architect_service
           -> tech_lead review -> parallel backend_service + frontend_service)
       2. Deterministic Patch: app/services/deterministic_patcher.py (6621 lines,
          the single largest file in the repo)
       3. Verify & Score: app/verification/engine.py::VerificationEngine (L1096)
          -> static validators, ContractConformanceValidator (app/contract/validator.py:41),
             runtime startup + endpoint smoke tests, user_journey_runner.py,
             app/confidence/engine.py, app/scoring/engine.py::ScoringEngine (L309)
       4. Repair loop: app/repair/orchestrator.py::FixOrchestrator (L903)
       5. Deploy (optional): app/deployments/*_provider.py
```

**Ten other generation endpoints coexist in `main.py`**, confirmed
still registered and reachable, not removed: `/generate`, `/project`,
`/project/v6` (L809), `/project/v7` (L900, its own `v7_orchestrator.py`),
`/project/v8` (L990), `/v9` (L1012), `/v10` (L1033), `/v11` (L1059),
`/v12` (L1143), `/v14` (L1182), plus `/project/tournament` (L827).
Per this project's own CLAUDE.md, `/project/v15` is the only one under
active development — the other ten are historical fallback surface
area, not dead code in the sense of being unreachable, but dead in the
sense of receiving no further engineering investment.

## 2. Repair pipeline

```
verification failures -> group_diagnostics() (app/repair/grouper.py)
  -> FixOrchestrator.run_attempt() (orchestrator.py:1027+) -> StrategyConfig
     escalation via app/retry/manager.py::RetryManager (5-level:
     patch_file -> regenerate_file -> regenerate_module -> switch_model
     -> regenerate_arch)
  -> _apply_fix_group() (L590) / _regenerate_module() (L779) /
     _regenerate_architecture() (L873) — the write-call-site map this
     project's own Experiments 066/067 already fully documented in
     docs/WRITE_PIPELINE.md
  -> deterministic + preflight patches run AFTER the LLM strategy
     (L1097-1107, wrapped in a bare `except Exception` that swallows a
     patcher crash to a print statement — flagged in docs/TECH_DEBT_MASTER.md)
  -> re-verify -> re-score -> regression check (score is the primary
     arbiter; diagnostic-count is only a tiebreaker, per L1118-1147's
     own comment documenting this itself was a prior bug fix)
  -> commit or revert via _ProjectSnapshot (L923-953, whole-project,
     extension-filtered snapshot taken before every attempt)
```

Full detail (strategy dispatch quirks, failure isolation, metrics,
known bugs) in `docs/REPAIR_INTELLIGENCE.md`.

## 3. Validation pipeline

```
app/verification/engine.py::VerificationEngine.run() (L1096, 1705 lines total)
  -> _run_compile_check() L96, _run_static_validators() L52,
     _run_import_closure_check() L241, _run_contract_conformance_check() L327,
     _run_runtime_validation() L465, _run_schema_db_assertion() L640,
     _run_llm_judge() L994, _diag() L1339
  -> app/verification/graph.py::FailureGraph/build_failure_graph()
     (L54/L83, dependency-aware failure graph, distinct from the
      repair grouper)

_run_static_validators() -> app/services/validator_service.py::validate_project()
  -> 23 validator calls across 12 wired files (see docs/VALIDATOR_INTELLIGENCE.md
     for the full dispatch order and the 2 confirmed-dead validator files)
```

## 4. Runtime pipeline

```
app/runtime/ (12 files):
  backend_runner.py (subprocess startup smoke test)
  -> error_parser.py (traceback classification)
  -> user_journey_runner.py (register->login->create->edit->delete
     live HTTP walk — the highest-complexity function in the repo per
     Exp059's own complexity scan, and this project's own Exp068 subject)
  -> playwright_runner.py / playwright_workflow.py (browser UX)
  -> vision_validator.py
  -> deployment_validator.py / deployment_error_parser.py / docker_validator.py
     (post-deploy checks)
  -> frontend_runner.py (Vite build)
  -> runtime_models.py (shared dataclasses)
```

## 5. Deployment pipeline

```
app/deployments/ (note: directory is named "deployments", not "deploy"):
  base_provider.py::BaseDeploymentProvider (ABC: deploy()/get_logs()/
    get_status() -> DeploymentResult dataclass)
  implemented by: cloudflare_provider.py, railway_provider.py,
    render_provider.py (+render_config.py), flyio_provider.py

Entry points: main.py:1236 (/deploy/github), :1243 (/deploy/railway),
  :1252 (/deploy/cloudflare)
```

Independently flagged this cycle (Fork 1) as "one of the better-designed
subsystems structurally" — a clean ABC pattern, no confirmed issues.

## 6. Observatory

```
GET /observatory (main.py:840-887)
  -> reads failure_memory/generation_log.jsonl +
     benchmark_results/canary_history.json directly off disk
  -> app/memory/reliability_metrics.py::compute_observatory() /
     compute_reliability_timeline() / compute_experiment_attribution() /
     compute_prevention_rate() (the SAME functions failure_report.py's
     CLI dashboard uses — confirmed single source of truth)
  -> app/memory/experiment_log.py::parse_recent_experiments()
     (parses experiments.md directly, last 8 entries)
  -> JSON response -> frontend/src/pages/Observatory.jsx
```

## 7. Benchmark pipeline

**Two parallel systems, not one** — a confirmed architectural
duplication risk, not resolved this cycle:
```
run_benchmark.py (main() L288, older/simpler)
run_forgebench.py (ForgeAIGenerator/Generator classes, run_suite() L182)
```
Both depend on the same `app/benchmark/` support modules: `loader.py`
(BenchmarkPrompt), `metrics.py` (BenchmarkResult/BenchmarkReport),
`reporter.py` (BenchmarkReporter), `heatmap.py`, `history.py`,
`kpi_dashboard.py`.

## 8. Provider layer

```
app/providers/ai_provider.py — dispatch hub, the auto-fallback chain
  (Cerebras -> Gemini -> Groq per this project's own CLAUDE.md),
  backed by cerebras_provider.py, gemini_provider.py, groq_provider.py,
  deepseek_provider.py, openai_provider.py, openrouter_provider.py,
  ollama_provider.py, plus model_router.py (model-selection, separate
  from provider fallback)
```
Confirmed the single highest-fan-in module in the repo (28 real
importers, per Exp065, re-verifiable via graphify) — the widest blast
radius of any change in the codebase.

## 9. Storage layer

Four distinct, independently-confirmed mechanisms — no unified store:

```
a) app/database.py — SQLAlchemy, ForgeAI's OWN relational DB (User,
   GenerationJob models in app/models/). DATABASE_URL env var;
   defaults to sqlite:////data/forgeai.db (Railway volume) or
   sqlite:///./forgeai.db (local). Auto-converts postgres:// ->
   postgresql:// (a documented Railway quirk).
b) app/queue/job_queue.py — a SEPARATE SQLite DB (job_queue.db),
   documented upgrade path to Redis (REDIS_URL). Atomic dequeue for
   N concurrent workers.
c) backend/failure_memory/*.json + *.jsonl — telemetry/memory store,
   fully mapped in Experiment 068 (patterns.json, generation_log.jsonl,
   bundles/, repair_db.json, cost_log.json, arch_db.json,
   design_fingerprints.json, etc).
d) backend/generated_projects/ (filesystem) + backend/llm_cache/
   (file-based LLM response cache, 5,901 files confirmed in Exp068).
```

## 10. Authentication (ForgeAI's OWN dashboard auth)

```
app/dependencies/auth.py (fully read):
  JWT via python-jose (SECRET_KEY env var, INSECURE HARDCODED
    DEFAULT if unset — see docs/SECURITY_REVIEW_V2.md Finding #1,
    ALGORITHM="HS256", ACCESS_TOKEN_EXPIRE_MINUTES=30)
  OAuth2PasswordBearer(tokenUrl="/login")
  bcrypt password verification via app/services/user_service.py

Endpoints: main.py:93 /register, :101 /login, :113 /me
```
`get_current_user()` gates `/jobs` list/get (`:599`, `:682`) — **not
confirmed whether it gates every state-mutating endpoint**, flagged
as Unknown, not exhaustively checked this cycle.

## 11. CLI

**No unified CLI framework found** — zero `click`/`typer` usage in
project code (`argparse` appears only inside `venv/` third-party
packages). Instead, ~8 independent, standalone argparse-based scripts:
`run_benchmark.py`, `run_forgebench.py`, `run_stability_bench.py`,
`benchmark_frontend.py`, `app/queue/worker.py`, `scripts/run_canary.py`,
`scripts/observatory.py`, and various `scripts/exp0*_*.py` one-off
investigation scripts. **This is a confirmed, real gap for a
commercial-maturity review**, not an oversight to gloss over — see
`docs/TECH_DEBT_MASTER.md`.

## 12. API

`main.py` is 1477 lines. **Only one `include_router()` call in the
whole file** (`:76`, the queue router) — every other endpoint (~45
counted via grep) is a direct `@app.get/post/delete/websocket`
decorator in this single file, no router-per-domain split. Groups:
auth (3), jobs (9, incl. 1 websocket), generation (12 versions), 7
cost/observatory/benchmark/improve/leaderboard/research/dataset
routes, 4 deployment-history/leaderboard/stats/memory routes + 3
deploy-action routes, credentials (3), health (1), download (1).

## 13. Frontend

```
frontend/src/App.jsx — React Router root (Routes/Route,
  PrivateRoute/PublicRoute wrappers, L27/33) — 8 routes confirmed
AuthContext.jsx + hooks/useAuth.jsx — auth state
pages/ (11 files): CredentialsPage, Dashboard, Generate, JobDetail,
  Landing, Login, NewProject, Observatory, Project, ProjectDetail,
  Register
components/ (5 files, mostly cinematic/visual): AuthScene, NavBar,
  Scenery/SceneryBoost/SceneryParticles, Veil
lib/ (api.js — API client, cinematic.js, pipelineStages.js)
```
**Possible duplication, not reconciled this cycle**: both a top-level
`api.js` and `lib/api.js` were found — Unknown which is the real
client or whether one is dead. No dedicated state-management library
confirmed (no Redux/Zustand import found) — likely plain React
Context + local state, not exhaustively verified.

## 14. Dependency graph (top-level box-and-arrow)

```
API (main.py)
  -> Generation (v6_orchestrator)
       -> Verification (engine.py)
            -> Repair (orchestrator.py)
                 -> back into Generation (the confirmed regenerate_arch
                    reverse-layer call, docs/ARCHITECTURE_REVIEW.md's
                    own finding)
                 -> Deployments
  -> Providers (ai_provider.py) — called FROM Generation, Repair, AND
     Benchmark; the widest fan-in node in the repo
  -> Storage (database.py, job_queue.py, failure_memory/) — written to
     FROM Generation/Repair/Verification, read FROM Observatory/Benchmark
  -> Runtime (runtime/) — called FROM Verification only
Frontend -> API exclusively via lib/api.js — no direct backend imports
  (confirmed by directory separation)
```
