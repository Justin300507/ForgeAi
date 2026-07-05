# ForgeAI VNext — Chief Architect Report

**Date:** 2026-07-06 · **Author:** Claude (Fable 5), Chief Architect session
**Audience:** Sonnet (incremental implementer). Every recommendation is scoped so it can be implemented as an isolated PR without breaking the running V15 pipeline.
**Ground rules honored:** no code was modified; all findings come from the graphify knowledge graph, `backend/failure_memory/*`, `backend/benchmark_results/history.json`, and targeted reads of the load-bearing modules.

---

## 1. Executive Summary

ForgeAI today is a **working but sedimentary system**. The V15 pipeline (`backend/app/core/pipeline.py`) is genuinely well-designed — plugin-injectable verification/scoring/repair, event bus, best-snapshot restore, stall detection — but it is a thin, modern shell around a 2026-era V6 generation monolith, nine coexisting orchestrator versions, a 5,293-line single-file deterministic patcher with 97 patch functions, and two parallel validation systems. Reliability gains over the last month came almost entirely from *reactive patching* (one deterministic patch per observed failure), not from *preventing* the failure classes at generation time.

The failure data is unambiguous: of the 98 recorded failure instances across 22 pattern types, roughly **60% are contract-coherence failures** — the backend generator, frontend generator, and architect disagreeing about names, imports, fields, and endpoints (`ImportError` 11, `SQLAlchemyError` 10, `ModuleNotFoundError` 9, `RouterExportMismatch` 8, `MissingEndpoint` 7, `NoReferencedTableError` 6, `PydanticSerializationError` 4, field-mismatch/relationship errors ~8). These are not model-creativity problems. They are **the absence of a single enforced source of truth** between generation stages.

The single highest-leverage change is therefore architectural, not prompt-level: **make the Architect's output a machine-validated Contract (typed IR), generate all code *from* the contract, and validate *against* the contract before any code executes.** Every recurring failure pattern above becomes either impossible-by-construction or detectable in <1s statically. This one change is projected to lift end-to-end success from the current ~65–78% band to ~85%, and it makes the fix loop's job qualitatively easier (fixes become "conform to contract" instead of "guess what's wrong").

Second-order priorities: (2) collapse the two validation systems into the VerificationEngine, (3) decompose the deterministic patcher into a rule registry with per-rule provenance and hit telemetry, (4) delete the v6–v14 orchestrators after extracting the ~4 functions V15 still reaches into, (5) fix the scoring neutral-50 flaw at the root instead of the `_frontend_build_ok` special-case, and (6) wire up the learning stores that are currently ornamental (generation_log has 2 records; deployment_memory has 3, all failures, none since June 22; cost attribution is 80%+ "unknown").

Target trajectory: **~65–78% today → 85% (contract) → 90% (validation+repair redesign) → 93% (deployment preflight) → 95%+ (learning loop compounding)**.

---

## 2. Top 20 Highest-ROI Improvements

Ranked by (success-rate impact × permanence) ÷ effort. Effort: S <1 day, M 1–3 days, L 1–2 weeks.

| # | Improvement | Root problem it kills | Effort | Est. Δ success | Section |
|---|---|---|---|---|---|
| 1 | **AppContract typed IR** — architect emits machine-validated contract; generators consume it; static contract-conformance check | ~60% of failures (imports, routers, fields, endpoints) | L | +10–15 pts | §5.2 |
| 2 | **Contract-conformance validator** (static, <1s, no LLM) run immediately after generation | Same class, caught before runtime | M | (included above) | §7 |
| 3 | **Single VerificationEngine** — retire `validator_service.validate_project` (1,195 lines) into engine verifiers | Two validation systems drift; fixes land in one | M | +2 | §7 |
| 4 | **Patch registry decomposition** — split `deterministic_patcher.py` (5,293 lines, 97 funcs) into `repair/rules/` with per-rule metadata: trigger, failure-pattern id, hit count, last-hit date | Unmaintainable immune system; dead rules invisible | L | +1 (indirect, velocity) | §8.4 |
| 5 | **Delete v6–v14 orchestrators**; extract deploy helpers V15 imports from V14 (`_push_to_github`, `_deploy_render`, `_deploy_cloudflare`, `_run_health_checks`) into `app/deploy/` as public API | Hidden coupling; 9 dead pipelines confuse every change | M | 0 direct, large maintainability | §4 |
| 6 | **Scoring: explicit N/A dimensions** — skipped stages score `None` and renormalize weights, never neutral-50 | Broken-frontend apps clearing deploy gate (already bitten once) | S | +2 | §7.3 |
| 7 | **Journey/CRUD test generation from contract** — derive Playwright CRUD journeys from contract entities instead of generic heuristics | JourneyCRUDFailure (12 = #1 pattern) | M | +3–4 | §7.2 |
| 8 | **Deployment preflight simulator** — run the *exact* Render/Cloudflare build commands locally in a clean env before any deploy call | Deploy failures discovered remotely; stale-code class | M | +2–3 deploy success | §9 |
| 9 | **Repair memory that actually fires** — key FixCache/repair_db on (pattern-id, contract-fingerprint), consult before every LLM fix call | repair_db has 9 entries and near-zero reuse | M | +2, −cost | §8 |
| 10 | **Failed-strategy memory in RetryManager** — persist per-(pattern, strategy) outcome; skip strategies with 0/N history for that pattern | Ladder retries known-useless strategies | S | +1, −cost | §8.2 |
| 11 | **Structured generator output (JSON file map with per-file syntax gate)** — reject/regen a single file on parse failure, not the whole response | FrontendBuildError (7), malformed-output retries | M | +2 | §6 |
| 12 | **Import closure check** at generation time: every import in every generated file must resolve within the file map or declared deps | ImportError+ModuleNotFoundError (20 combined) | S | +3 (overlaps #1) | §7 |
| 13 | **Stage-attributed cost tracking** — `by_stage` is "unknown" for 80%+ of calls; pass stage through `generate_content` | Can't optimize what isn't measured | S | 0 direct | §13 |
| 14 | **Wire generation_log + deployment_memory into V15 for real** (2 and 3 records respectively; deployment_memory last updated Jun 22) | Learning loop starved of data | S | 0 direct, enables #9/#10 | §8.5 |
| 15 | **Frontend design-system template hardening** — ship the design system as *static verified files* (tokens, primitives, layouts) and have the LLM generate only feature components | UI variance, JSX build breaks in boilerplate | M | +2, big UI quality | §10 |
| 16 | **Per-benchmark expected-failure assertions** in ForgeBench (each suite app declares its known failure modes; regressions alert) | Benchmarks measure score, not *why* | M | 0 direct, protects gains | §11 |
| 17 | **Prompt modularization** — decompose 707-line frontend prompt into composable sections (contract, design system, page spec, rules) assembled per-app | Prompt drift, unverifiable rules | M | +1–2 | §6 |
| 18 | **Runtime validator: schema-vs-DB assertion** — after boot, introspect SQLAlchemy metadata vs. Pydantic response models; fail fast with exact field diff | Pydantic/response_model 500s class | S | +1 | §7 |
| 19 | **Backend-root hygiene** — move `test_run*.txt`, `diagnose_runtime*.py`, one-off scripts to `scripts/` & `artifacts/` (gitignored); llm_cache out of git | Repo noise; the git status is unreadable | S | 0 direct | §4 |
| 20 | **Parallel verification stages** (frontend build ∥ runtime boot ∥ static) — engine docstring claims parallel; confirm and extend | ~2 min wall time per app; faster loops = more fix budget | M | 0 direct, −40% latency | §12 |

**Implementation order for Sonnet:** #6, #10, #12, #13, #14, #18, #19 first (all S, independent, immediate value) → #2+#1 (the contract, in two PRs: schema+validator first, generator adoption second) → #3, #7 → #5, #4 → #8, #9, #11 → the rest.

---

## 3. Top Recurring Failure Patterns (evidence-based)

Source: `backend/failure_memory/patterns.json` (98 instances, 22 types, window 2026-06-21 → 2026-07-05).

| Rank | Pattern | Count | Category | Root cause | Arch or prompt? | Permanent fix |
|---|---|---|---|---|---|---|
| 1 | JourneyCRUDFailure | 12 | Browser/CRUD | Journeys are generic heuristics, not derived from the app's actual entities/routes; timing + selector fragility | Architectural | Generate journeys from AppContract entities (#7); wait-on-network idle; data-testid contract in frontend prompt |
| 2 | ImportError | 11 | Backend runtime | Generators invent module paths; no import-closure check | Architectural | Contract file map + import closure validator (#1, #12) |
| 3 | SQLAlchemyError | 10 | Database | Relationships referencing unregistered tables/models; stripped relationships; monolithic schema drift | Architectural | Contract-declared relationships; model registration check pre-runtime |
| 4 | ModuleNotFoundError | 9 | Backend runtime | Same as ImportError; also wrong-path imports (auth utils) | Architectural | Same as #2 |
| 5 | RouterExportMismatch | 8 | Backend | Route file name → expected export name convention is *implicit*; LLM guesses | Architectural | Contract names the export; validator enforces (already partially deterministic-patched — promote to prevention) |
| 6 | FrontendBuildError | 7 | Frontend | Nested-brace JSX, malformed files, icon imports; whole-response parse failures | Prompt + arch | Per-file syntax gate at write time (#11); flattened-JSX rules already in prompts — keep; esbuild parse check per file |
| 7 | MissingEndpoint | 7 | CRUD/API | Backend generates subset of endpoints the frontend calls | Architectural | Contract endpoint list; conformance check both sides |
| 8 | NoReferencedTableError | 6 | Database | FK to table that doesn't exist / wrong `__tablename__` | Architectural | Contract schema is the only source of table names |
| 9 | PydanticSerializationError + ResponseValidationError + ModelFieldMismatch | 6 | Backend | Schema fields ≠ model fields (`completed` vs `is_complete`) | Architectural | Contract field list shared by model+schema generators; static diff check (#18) |
| 10 | ValidationError / misc runtime | ~8 | Runtime | Long tail: config attrs, Depends misuse, not-null seeds | Mixed | Preflight rules (exists), each new instance becomes a *registry rule with telemetry* (#4) |

**The meta-pattern:** rows 2,3,4,5,7,8,9 (57 instances, 58%) are all the same disease — **cross-file/cross-stage name-and-shape disagreement**. The current system treats each symptom with a deterministic patch after the fact (hence 97 patch functions). The permanent cure is a single typed contract that all generators consume and all validators check against. That is why #1 dominates the ROI table.

**Fix-loop pathology note:** the git log itself documents the reactive cycle (e.g. "Reject fix-loop patches with unbalanced JSX tags", "fix orphan-route template cloning", "hardcoded /dashboard redirect"). Each commit is a good patch; the trajectory is whack-a-mole. The patch registry (#4) at least makes the mole population measurable; the contract (#1) shrinks it.

---

## 4. Complete Architecture Review (ranked issues)

### What exists (verified via graphify)

```
V15Pipeline.run()                          core/pipeline.py (527 L)  ← current entry
 ├─ model_router.route()                   providers/
 ├─ _generate() → generate_project_v6()    services/v6_orchestrator.py (1,103 L)
 │    └─ plan → architect → backend → frontend   (planner/architect/backend/frontend services + 21 prompt files)
 ├─ _deterministic_patch()
 │    ├─ run_deterministic_patches()       services/deterministic_patcher.py (5,293 L, 97 funcs)
 │    ├─ database_patcher (6 entry funcs)
 │    └─ repair/preflight registry
 ├─ VerificationEngine.run()               verification/engine.py (1,065 L, 11 stages)
 ├─ ScoringEngine.score()                  scoring/engine.py (10 dimensions)
 ├─ Fix loop: RetryManager (5-strategy ladder) + FixOrchestrator (repair/orchestrator.py, 1,100 L)
 │    strategies: PATCH_FILE → PATCH_FILE+ → REGENERATE_MODULE → SWITCH_MODEL → REGENERATE_ARCH
 │    stall-stop after 3 non-improving; best-_ProjectSnapshot restore
 └─ _deploy() → imports V14 PRIVATE funcs (_push_to_github/_deploy_render/_deploy_cloudflare/_run_health_checks)
```

Plus: job API (`main.py` + queue/), batch runner, ForgeBench, knowledge stores (arch_db, failure_db, repair_db, component_db), confidence engine, replay recorder.

### Ranked issues

**P0 — Reliability-limiting**
1. **No enforced inter-stage contract.** The architecture JSON is advisory prose for the generators; nothing validates that backend routes, frontend API calls, models, and schemas agree. Direct cause of failure ranks 2–9 (§3).
2. **Two validation systems.** `validator_service.validate_project` (1,195 L, legacy, called from V6 path) and `VerificationEngine` (11 stages, called from V15). Static validators exist in both; fixes must land twice; they *have* drifted (schema/model validators live as separate services: `schema_model_validator`, `orm_validator`, `import_validator`, `router_export_validator`, `symbol_validator`, `undefined_symbol_validator`, `session_validator`, `stub_handler_validator`, `duplicate_class_validator`, `global_statement_validator`, `self_shadow_validator` — 11 standalone validator services with no shared interface).
3. **Scoring neutral-50 for skipped dimensions** (documented in pipeline.py's own comment, worked around with `_frontend_build_ok`). The workaround handles frontend-build only; the same hole exists for any other skipped dimension.

**P1 — Maintainability-limiting (slows every future fix)**
4. **Nine orchestrator generations coexist** (v6–v15 + project_service). V15 *depends on* V6 (generation) and V14 (deploy privates). No one can safely delete anything; graphify shows call edges into v6/v7/v11/v14 from live code and tests.
5. **deterministic_patcher.py: 5,293 lines, 97 functions, one file.** No per-rule provenance, no hit telemetry, no way to know which of the 97 rules still fire. Rules are the system's accumulated immune memory and they're unqueryable.
6. **79 services in one flat directory**, mixing live pipeline services, dead versioned orchestrators, validators, reviewers, and utilities. Plus root-level litter (`test_run1..16.txt`, `diagnose_runtime{,2,3}.py`, `'` and `0.6` and `list[str]` directories(!), response dumps).
7. **Prompt monoliths.** frontend_prompt 707 L, runtime_fix_prompt 691 L, backend_prompt 577 L of accreted rules. No structure separating invariants / app-specific spec / style rules; every new failure adds a paragraph nobody can verify still matters.

**P2 — Learning/observability gaps**
8. **Learning stores are ornamental.** generation_log: 2 records (both test data). deployment_memory: 3 records, all failures, none since 2026-06-22 (the system HAS deployed successfully since — simple_todo went live 2026-07-02 — so the store is disconnected). repair_db: 9 entries. cost_log: 254 entries but `by_stage` ≈ "unknown".
9. **Hidden env-flag configuration** scattered as `os.environ.get` (FORGE_MAX_STALLED_FIX_ATTEMPTS, FORGE_DEPLOY_THRESHOLD, …) with no single config module or documentation.
10. **Benchmark discipline regressed**: last full-suite run (20 apps) was 2026-06-28 (77.8 weighted); everything since is 1–2-app verify runs. There is no current honest baseline for the success-rate claims.

---

## 5. Proposed VNext Architecture

### 5.1 Design principles
1. **One source of truth per concern.** The Contract owns names/shapes; the VerificationEngine owns "is it correct"; the ScoringEngine owns "how good"; the rule registry owns deterministic repair.
2. **Prevent > detect > repair**, in that order of investment. Every repair rule that fires ≥3 times must graduate into either a contract constraint (prevention) or a generation-time gate.
3. **Everything measurable.** Every rule, prompt section, strategy, and stage carries an id; every firing is logged with that id.
4. **Delete aggressively.** A version that is not the current pipeline is either extracted-and-deleted or deleted.

### 5.2 The AppContract (the keystone)

A Pydantic model, produced by the Architect stage, validated before any code generation:

```
AppContract
  app: {name, category, description}
  entities: [{name, table_name, fields: [{name, type, nullable, default}],
              relationships: [{kind, target, back_populates, cascade}]}]
  endpoints: [{method, path, name, entity, request_schema, response_schema,
               auth_required, status_codes}]
  schemas:   [{name, entity, fields, orm_mode}]        # derived, not free-form
  frontend:  {routes: [{path, page, guards}],
              api_calls: [{endpoint_ref, page}],        # must reference endpoints
              components: [name], design_tokens_ref}
  files:     [{path, kind, exports: [name]}]            # the whole file map
  deps:      {python: [...], npm: [...]}
```

- **Generators receive the contract**, not prose. The backend prompt says "implement exactly these endpoints/models/schemas"; the frontend prompt says "call exactly these endpoints".
- **ContractConformanceValidator** (pure Python, <1s): every file in `files` exists with declared exports; every import resolves within the file map or deps; every frontend api_call references a declared endpoint; every schema field ⊆ entity fields; every FK target exists; router export names match. This validator alone would have caught 57 of the 98 recorded failures before a single process started.
- **The fix loop gains a target**: diagnostics become "file X violates contract clause Y", which is a dramatically better LLM fix prompt than a raw traceback.
- Migration path: introduce the contract as *derived* from the current architect output first (adapter), then tighten the architect prompt to emit it natively. No big-bang.

### 5.3 Module layout (target)

```
app/
  contract/        # AppContract models + conformance validator
  generation/      # planner, architect, backend_gen, frontend_gen (consume contract)
  prompts/         # composable sections (see §6), assembled by PromptBuilder
  verification/    # THE validation engine; all validators implement one Verifier protocol
  scoring/
  repair/          # orchestrator + rules/ registry (see §8) + preflight
  deploy/          # providers + preflight simulator + github (public API, extracted from v14)
  knowledge/       # arch_db, failure_db, repair_db, cost — all actually wired
  pipeline.py      # V15Pipeline, unchanged shape
scripts/           # one-off diagnostics, moved out of backend root
```

Data flow stays linear and event-bus-observed exactly as V15 has it — that part of the design is right and is preserved.

### 5.4 Why this is superior
- **Determinism:** names/shapes are decided once, by one stage, and mechanically propagated. The #1 empirical failure class becomes structurally impossible.
- **Repair quality:** contract-referenced diagnostics collapse root-cause analysis; the fixer stops guessing.
- **Maintainability:** one validation engine, one rule registry with telemetry, zero dead orchestrators.
- **Scalability:** contract + file map enables safe parallel generation (backend and frontend generated concurrently against the same contract — today parallelism risks divergence; with a contract it doesn't).

---

## 6. Prompt Redesign

Principle: **prompts stop being rulebooks and become contract renderers.** Current prompts accumulate imperative rules ("never nest braces in JSX", "always export `<name>_router`") because generation has no ground truth. With the contract, ~40% of current prompt text becomes mechanical input data.

Structure every generator prompt as five composable sections (PromptBuilder assembles; each section versioned, with an id logged per call):

1. **ROLE+OUTPUT-FORMAT** (invariant, ~20 lines): JSON file-map output, one file per key, no markdown fences. Malformed-output prevention lives here only.
2. **CONTRACT** (rendered from AppContract): entities, endpoints, file map with expected exports. *Data, not prose.*
3. **INVARIANT RULES** (small, curated, each rule carries the failure-pattern id that justified it — rules with no firing pattern in 30 days get reviewed for deletion). Current 700-line prompts shrink to <150.
4. **DESIGN/STYLE** (frontend only): design-system tokens + component conventions (§10).
5. **FEW-SHOT ANCHOR** (one small canonical file pair, e.g. a model+schema+route trio that demonstrably passes validation — sourced from component_db's best-scoring output, refreshed automatically).

Per-agent notes:
- **Planner:** keep short; its only contract obligation is feature list + category (drives arch_db lookup and design palette).
- **Architect:** the big rewrite — output = AppContract JSON, validated by Pydantic before proceeding; on validation error, re-ask with the error (max 2 tries) — this replaces downstream failure with a cheap upstream retry.
- **Backend/Frontend generators:** consume contract; forbidden from inventing files, routes, or fields not in it; may *request* contract amendments via a declared `contract_gaps` output field (logged, applied by a deterministic amender) rather than silently diverging.
- **Fix/Runtime-fix agents:** input = failing diagnostic + contract clause + minimal file slice (not whole project); output = unified-diff or full-file JSON keyed by path; must state `root_cause` and `strategy` fields — these feed the failed-strategy memory (#10).
- **Deployment agent:** replaced mostly by deterministic preflight (§9); LLM only for novel deploy-log triage.

Expected effects: fewer malformed outputs (single canonical output format), fewer retries (upstream contract retry is cheaper than downstream fix loop), measurable rules (every rule ties to a pattern id).

---

## 7. Validation Engine Redesign

Keep the 11-stage VerificationEngine as the single engine; retire `validator_service.validate_project` by porting any unique checks into engine verifiers implementing one `Verifier` protocol (`name`, `stage`, `cost_class`, `run(ctx) -> VerificationResult`).

### 7.1 Optimal execution order (cheap→expensive, prevention→observation)

| Order | Stage | Cost | Rationale |
|---|---|---|---|
| 1 | Structure + dependency manifest check | ms | Fail before reading file contents |
| 2 | **Contract conformance** (new) | <1s | Kills the 58% class before any process starts |
| 3 | Python compile + import closure | ~1s | Static, catches ImportError/ModuleNotFound without booting |
| 4 | TS/ESLint + per-file esbuild parse | ~2s | Catches JSX breakage per-file with exact locations |
| 5 | Frontend production build ∥ Backend boot | 30–60s | Run in parallel (independent); boot with drain threads (already fixed) |
| 6 | DB assertion: SQLAlchemy metadata ↔ schema fields ↔ contract | ~1s post-boot | Catches field-mismatch 500s before HTTP tests waste time |
| 7 | Auth + endpoint smoke tests (from contract endpoint list) | 5–15s | Every declared endpoint must answer; strictness already added in V17 |
| 8 | CRUD API round-trips (create→read→update→delete per entity) | 10–30s | Data-level correctness before browser cost |
| 9 | Browser: load, console-error scan, screenshots | 30–60s | Only meaningful once API works |
| 10 | CRUD journeys via Playwright (generated from contract) | 60s | The #1 failure pattern gets contract-derived selectors (`data-testid={entity}-{action}`) |
| 11 | Accessibility + performance + LLM judge | varies | Quality, not correctness — never gates deploy alone |

**Why this order:** each stage only runs when everything cheaper has passed, so expensive stages never re-discover cheap failures; and each failure surfaces at the layer with the *best diagnostic* (a contract violation reads better than the traceback it would later cause). Stages 5's parallelism is the main latency win (§12).

### 7.2 Journey testing fix (pattern #1)
Generate journey specs from the contract: for each entity, a create/list/edit/delete journey using `data-testid` selectors that the frontend prompt is contractually required to emit. Replace time-based waits with network-idle + element-state waits. Record journey failures with the failing step id, not just "journey failed".

### 7.3 Scoring fix
Dimensions for stages that did not run score `None`; ScoringEngine renormalizes remaining weights and reports coverage (e.g. "score 91 over 8/10 dimensions"). Deployment gate = threshold on renormalized score **and** zero FAILED critical stages (build, boot, contract). Delete the `_frontend_build_ok` special case once this lands — it becomes redundant.

---

## 8. Fix Agent Redesign

The current bones are good (strategy ladder, snapshot revert, stall-stop, best-state restore). The gaps are memory, root-cause targeting, and rule governance.

### 8.1 Repair decision tree

```
Diagnostics in → group by (pattern_id, file cluster)
├─ 1. Rule registry match?            → apply deterministic rule (free, <1s), re-verify affected stage only
├─ 2. Repair memory hit?              → apply cached fix keyed (pattern_id, contract_fingerprint); re-verify
├─ 3. Contract violation?             → LLM patch prompted with the violated clause + minimal file slice
├─ 4. Runtime traceback?              → root-cause pass first (name the causal file+line+why, 1 cheap call),
│                                        then patch the *cause* file, not the symptom file
├─ 5. Multi-file/systemic (≥3 files, same cause)? → REGENERATE_MODULE from contract
├─ 6. Same pattern failed twice with same strategy? → strategy is BANNED for this run (failed-strategy memory),
│                                        escalate: SWITCH_MODEL → REGENERATE_ARCH (contract re-emit)
└─ 7. Score stalled 3 attempts        → stop, restore best snapshot (unchanged from today)
```

### 8.2 Regression prevention (strengthen what exists)
- Keep full-snapshot + score-gated revert (V15 already has it).
- Add **per-stage regression check**: a fix that flips any previously-PASSED stage to FAILED is reverted immediately, without waiting for the aggregate score (aggregate can mask a regression when another dimension improves).
- Verify only affected stages after deterministic rules (cheap), full verify after LLM fixes.

### 8.3 Adaptive memory
- `failed_strategies[(pattern_id, strategy)] → {tries, successes}` persisted in failure_memory; RetryManager consults it (skip strategies with 0/≥3 for the pattern).
- Every successful LLM fix is distilled into repair_db keyed (pattern_id, contract_fingerprint) — today's repair_db (9 entries) is keyed by fix-hash, which never matches again.

### 8.4 Rule registry (replaces the 5,293-line patcher)
`repair/rules/<domain>_<name>.py`, each exposing `RULE = Rule(id, pattern_id, detect(ctx), apply(ctx), added, last_hit, hit_count)`. A registry runner replaces `run_deterministic_patches`. Migration: mechanical extraction of the 97 functions, wrapping each; behavior-identical, then telemetry reveals dead rules. **Governance:** any rule with hit_count ≥3 in 14 days triggers a "graduate to prevention" task (contract constraint or prompt invariant).

### 8.5 Wire the learning loop
generation_log.record and deployment memory writes must be verified to fire in the live V15 path (current record counts prove they effectively don't). Add a post-run assertion in ForgeBench that the run appended to both stores — the benchmark fails if telemetry is broken, which is what keeps it honest.

---

## 9. Deployment Redesign

Deploy failures are expensive (remote, slow, opaque logs). Strategy: **make deploy boring by proving it locally first.**

1. **Preflight simulator (the big one):** in a clean temp env, run exactly what Render/Cloudflare will run: `pip install -r requirements.txt` into a fresh venv + `uvicorn` boot with production env vars; `npm ci && npm run build` with production API URL baked. Any failure is a local diagnostic with the fix loop still available. Deploy is only attempted after preflight passes → deployment success becomes ≈ preflight success.
2. **Extract `app/deploy/`** from V14 privates: `push_to_github`, `deploy_render`, `deploy_cloudflare`, `run_health_checks` as public, tested functions (V15's `_deploy` imports become one-line changes).
3. **Config generation from contract:** `render.yaml`, build commands, env var manifest, CORS origins, and the DB-init path (the b70963d lazy-create fix) generated from the contract's deps + endpoints — not scraped from generated code.
4. **Post-deploy verification:** health checks exist; add one contract-derived CRUD round-trip against the live URL before declaring success, and *record the result in deployment_memory* (currently dead since Jun 22).
5. **Stale-code guard:** keep the push-before-deploy ordering (already fixed); add commit-SHA assertion — the deployed service must report the SHA just pushed (embed SHA in a `/health` field at generation time).

---

## 10. Frontend Quality (world-class UI generation)

The reliable path to Linear/Vercel/Stripe-level polish is **less LLM, more system**:

1. **Static verified design system shipped as template files** (never LLM-generated): design tokens (CSS variables per category palette — V13's palettes feed this), primitives (Button, Input, Card, Modal, Table, EmptyState, Skeleton, Toast — each with loading/disabled/error states, dark mode, focus rings, ARIA), layout shells (sidebar app shell, auth pages, settings). These files are hand-verified once and copied into every project — they cannot break the build.
2. **LLM generates only feature components** (entity tables, forms, dashboards) that *compose* primitives, under contract obligation to use `data-testid` hooks (feeds §7.2).
3. **Category-aware theming** (exists from V13) selects token values, not component code.
4. **Quality gates:** the existing vision validator + LLM judge score against a concrete rubric (spacing scale adherence, empty/loading states present per page, contrast, responsive breakpoints) instead of general "looks good".
5. **Forms:** generated from contract schemas (field types → input kinds, nullable → required marks, server errors mapped to fields). Deterministic, and it kills a whole class of form bugs.

This inverts today's ratio (LLM writes everything, patcher fixes JSX) to: system provides the 80% that must be flawless; LLM provides the 20% that must be app-specific.

---

## 11. Benchmark Suite (15 apps)

Shared expectations for every app: React+Vite+Tailwind frontend, FastAPI+SQLAlchemy backend, JWT auth unless noted, full CRUD per entity, deployable via Render+Cloudflare, premium UI per §10. Evaluation = Forge Score (renormalized, §7.3) + stage pass/fail vector + declared failure-mode assertions (#16).

| # | App | Entities (schema core) | Distinctive requirements | Expected score | Gen time | Known failure modes to assert |
|---|---|---|---|---|---|---|
| **Easy** | | | | | | |
| 1 | Todo | User, Task(priority,due,completed) | Filters, priorities | ≥95 | ≤6 min | field-mismatch (completed/is_complete) |
| 2 | Notes | User, Note(title,body,tags), Tag | Search, tag M2M | ≥95 | ≤6 min | M2M association table FK |
| 3 | Calculator | (no DB) History(expr,result) optional | Frontend-heavy; precision; keyboard | ≥95 | ≤4 min | scoring N/A dims (backend-light) |
| 4 | Weather Dashboard | Location, (external API mock/provider) | Async fetch, loading/empty/error states | ≥90 | ≤6 min | external-API env var wiring |
| 5 | Expense Tracker | User, Expense(amount,category,date), Category | Charts, monthly aggregates | ≥92 | ≤7 min | aggregate endpoint missing |
| **Medium** | | | | | | |
| 6 | Blog CMS | User(role), Post(status), Comment, Category | Draft/publish workflow, rich text, roles | ≥88 | ≤10 min | comment→post FK; role guards |
| 7 | Inventory | Product, Supplier, StockMovement, Location | Stock math, low-stock alerts | ≥88 | ≤10 min | movement math consistency |
| 8 | Library | Book, Member, Loan, Reservation | Due dates, availability state machine | ≥86 | ≤10 min | loan state transitions |
| 9 | Hospital | Patient, Doctor, Appointment, Prescription | Scheduling conflicts, sensitive-field handling | ≥84 | ≤12 min | appointment overlap logic |
| 10 | Student Mgmt | Student, Course, Enrollment, Grade | Enrollment M2M, GPA calc, transcripts | ≥86 | ≤12 min | enrollment uniqueness; grade calc |
| **Hard** | | | | | | |
| 11 | CRM | Contact, Company, Deal(stage), Activity, Pipeline | Kanban pipeline, activity timeline | ≥82 | ≤15 min | drag-drop journey; deal-stage enum drift |
| 12 | HR Mgmt | Employee, Department, LeaveRequest, Payroll, Review | Approval workflows, role hierarchy | ≥80 | ≤15 min | workflow state + role guards |
| 13 | E-Commerce | Product, Variant, Cart, Order, OrderItem, Payment(mock) | Cart/checkout flow, stock decrement, order lifecycle | ≥78 | ≤18 min | cart→order transaction integrity |
| 14 | LMS | Course, Module, Lesson, Enrollment, Quiz, Attempt | Nested content, progress tracking, quiz scoring | ≥78 | ≤18 min | deep nesting (4-level) routes |
| 15 | Project Mgmt | Project, Task(status,assignee), Sprint, Comment, Attachment | Kanban+list views, assignments, activity feed | ≥80 | ≤18 min | multi-view state sync; attachment handling |

Cadence: full 15-app suite weekly + before/after any P0/P1 change; per-app history in `benchmark_results/history.json` (restores the discipline lost since Jun 28). Success-rate KPI = fraction scoring ≥ deploy threshold with zero critical-stage failures.

---

## 12. Performance Optimization Opportunities

1. **Parallelize verification stage 5** (frontend build ∥ backend boot ∥ lint): −40–60s of the ~2 min wall time.
2. **Affected-stage-only re-verification** after deterministic rules (full verify only after LLM fixes): fix loop iterations drop from ~60s to ~5s for rule-fixed issues.
3. **Parallel backend+frontend generation from contract** (safe once contract lands): −30–50% generation latency.
4. **npm/pip warm caches** for verification/preflight envs (persistent cache dir keyed by lockfile hash).
5. **Vite build memory** (OOM fix exists) — also set `build.minify=false` during *verification* builds (correctness only; preflight/deploy keeps prod settings).

## 13. Cost Optimization Opportunities

1. **Fix stage attribution** (cost_log `by_stage` ≈ "unknown") — one `stage=` param through `generate_content`. Everything else depends on seeing where tokens go.
2. **Repair memory + rule registry before LLM** (#9): each cache hit saves a fix call (~5–15k tokens).
3. **Contract retry beats fix-loop retry:** re-asking the architect (~3k tokens) instead of a downstream fix cycle (~15k+ and a full re-verify) whenever the failure is contractual.
4. **Model routing per stage with outcome feedback** (router exists): route patch-level fixes to cheap/free-tier (chain exists from credit-optimization work); reserve premium models for architect + module regen. Feed fix success-rates back into routing.
5. **Prompt shrinkage from §6** (~40% of generator prompt tokens are removable rules once the contract carries the data): direct per-call input-token saving.

## 14. Risk Assessment

| Risk | Likelihood | Mitigation |
|---|---|---|
| Contract migration destabilizes a working pipeline | Medium | Adapter phase first (derive contract from current architect output; validator warn-only for 1 week of benchmarks before enforcing) |
| Deleting v6–v14 breaks hidden imports | Medium | graphify `path` check per module before deletion; extract-then-delete; keep one release tag |
| Patch-registry migration alters behavior | Low | Mechanical wrap of existing functions; golden-project regression fixtures (fixture_loader exists) run before/after |
| Rule telemetry reveals rules masking real bugs | Certain (that's the point) | Graduation process (§8.4) turns each into prevention |
| Benchmark cost of weekly 15-app suite | Low | ~$0.02–0.03/app measured → <$0.50/suite; trivial |
| Over-constraining generators hurts app diversity | Low-Med | Contract constrains *names/shapes*, not features; `contract_gaps` amendment channel preserves flexibility |

## 15. Long-Term Roadmap & Success-Rate Projection

Baseline honesty: last full-suite measurement is 77.8 weighted / 17-of-20 passed (2026-06-28); July single-app runs and the July-05 pattern entries indicate the current band is **~65–78%** depending on difficulty mix. First action of the roadmap is re-running the full suite to fix the baseline.

| Milestone | Contents (ROI #s) | Effort | Projected success rate |
|---|---|---|---|
| M0 (week 1) | Baseline re-run + quick wins: #6 #10 #12 #13 #14 #18 #19 | ~4 days | 78–80% |
| M1 (weeks 2–3) | **Contract**: schema, adapter, conformance validator warn→enforce (#1 #2), architect prompt v2 | ~8 days | **~85%** |
| M2 (week 4) | Validation unification (#3), journey-from-contract (#7), scoring cleanup complete | ~5 days | ~88% |
| M3 (week 5) | Repair: rule registry (#4), repair memory (#9), failed-strategy memory, decision tree (#8) | ~6 days | ~90% |
| M4 (week 6) | Deployment preflight + deploy/ extraction + SHA guard (#5 #8) | ~4 days | ~93% (E2E incl. deploy) |
| M5 (weeks 7–8) | Frontend design-system hardening (#15), prompt modularization (#17), parallel generation | ~7 days | ~94% |
| M6 (ongoing) | Learning loop compounding: rule graduation, routing feedback, weekly suite + failure-mode assertions (#16) | continuous | **95%+** |

Projections assume the §3 pattern distribution is representative; M1's jump is the arithmetic consequence of eliminating the 58% contract-coherence class at ~70% effectiveness. Each milestone ends with a full ForgeBench suite run; a milestone that doesn't move the suite number gets root-caused before proceeding — the same discipline the platform itself is being built to have.

---

*Implementation note for Sonnet: work top of §2 downward; every item names its section for full context. Do not start M1 before M0's baseline run exists — every later claim is measured against it.*
