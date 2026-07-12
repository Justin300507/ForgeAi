# ForgeAI Deep Architecture Review (Experiment 065)

2026-07-12. Offline-first Principal Engineer audit, ~zero API spend.
Covers Part 1 (Dependency Graph), Part 2 (Complexity — delta against
Exp059), Part 4 (State Management), and Part 6 (Documentation Coverage).
See `docs/RELIABILITY_REVIEW.md`, `docs/PERFORMANCE_REVIEW.md`,
`docs/SECURITY_REVIEW.md`, `docs/DEAD_CODE_REVIEW.md`,
`docs/TEST_QUALITY_REVIEW.md` for the remaining parts. Every claim below
cites real file:line evidence actually read, not estimated.

---

## Part 1 — Dependency Graph

**Layer membership** (confirmed by real imports, not directory-name
assumption):

- **API**: `main.py` (1477 lines), `app/routes/*.py`.
- **Services**: `app/services/*.py` — the largest cluster.
- **Repair**: `app/repair/orchestrator.py`, `preflight.py`, `grouper.py`.
- **Validators**: `app/services/*_validator.py` (13 files, Exp060's
  migration target) + `app/verification/engine.py`.
- **Generators**: the V6 team services (`product_manager_service`,
  `architect_service`, `tech_lead_service`,
  `backend_service`/`parallel_backend_service`, `frontend_service`).
- **LLM Providers**: `app/providers/ai_provider.py`.
- **Storage**: `app.database`, `app/memory/failure_memory.py`,
  `app/utils/{cost_tracker,llm_cache}.py`.
- **Deployment**: `app/deployments/{cloudflare,railway}_provider.py`.

### Methodology finding (important — affects every future dependency analysis of this repo)

A naive fan-in grep initially showed `app.database: 32`,
`app.models.user: 16`, `app.schemas.user: 7` importers. **These are
false positives from `app/prompts/backend_prompt.py` and
`app/prompts/runtime_fix_prompt.py`**, which embed literal example
import strings as instructional text for the generator LLM (e.g.
`"from app.models.user import User"`), not real ForgeAI imports.
Excluding `app/prompts/` dropped `app.database` from 32→11 real
importers and removed `app.models.user`/`app.schemas.user` from the
top-15 entirely. **Any future dependency analysis of this codebase must
exclude `app/prompts/*.py` from import-grep.**

### Hidden dependencies — confirmed real, not hypothetical

**`main.py` itself carries the largest hidden-dependency risk in the
repo**: 76% of its `app.*` dependencies (36 of 47) are function-local
imports (`from X import Y` inside a function body), invisible from a
top-of-file glance — e.g. lines 160/272/1198 (`v14_orchestrator`),
190/1227 (`v15_orchestrator`), 445 (`deterministic_patcher.run_frontend_patches`),
487 (`deployed_fixer.fix_deployed_app`). **This is the exact hidden-dependency
shape that caused the confirmed Exp053→056→057 regression** (a
function-local import losing scope when refactored) — `main.py` carries
far more of this risk than any single service file, and is worth a
dedicated future audit before any large `main.py` refactor.

### Layering inversion — confirmed real, with a direct consequence for Exp064

`app/repair/orchestrator.py:866` locally imports
`app.services.v6_orchestrator.generate_project_v6` — the repair layer
calls back into the full generator pipeline as its `regenerate_arch`
last-resort strategy. Not a load-time circular import (`v6_orchestrator.py`
has zero `app.repair` imports, confirmed), but a real, deliberate
reverse-layer dependency.

**Consequence, directly relevant to Exp064's new semantic-write guard**:
this call path writes files via `generate_project_v6`'s own internal
write mechanism, **not via `write_fix`** — meaning **Exp064's semantic-consistency
check does not cover repairs made through `regenerate_arch`/`regenerate_module`**.
This is a real, confirmed gap in that guard's coverage, found one cycle
later — flagged for a future experiment, not fixed here.

### Cycles

None found among the top 5 highest-risk service files
(`deterministic_patcher`, `database_patcher`, `file_writer_service`,
`validator_service`, `v6_orchestrator` — full cross-import matrix
checked). The repair→generator reverse dependency above is a one-way
call, not a cycle.

### God modules (true fan-in, methodology-corrected)

| Module | Real fan-in |
|---|---|
| `app.providers.ai_provider` | 28 |
| `app.utils.json_cleaner` | 27 |
| `app.core.context` | 22 (the Diagnostic/ErrorCategory layer Exp060 extended) |
| `app.services.deterministic_patcher` | 20 |
| `app.services.database_patcher` | 11 |
| `app.database` | 11 |

`app.providers.ai_provider` and `app.core.context` are the two
highest-consequence single-points-of-change in the codebase — any
signature change to either has the widest blast radius of anything in
the repo. `app.core.context` is worth particular attention since it was
just extended (Exp060) and is a candidate for further extension per
Exp064's own "future extensions" list.

---

## Part 2 — Complexity Audit (delta against Exp059)

Exp059 (same cycle, `docs/ENGINEERING_REVIEW.md` if still present)
already produced a full AST-based complexity survey of the ~56K-line
backend. Re-verified the top-file ranking is materially unchanged, and
measured what's new since:

**Top files, current vs. Exp059's snapshot:**
```
6621  app/services/deterministic_patcher.py   (unchanged, still #1)
3713  app/knowledge/lucide_icon_exports.py     (unchanged — static data)
1705  app/verification/engine.py               (was 1692, +13, Exp060's adapter boundary)
1314  app/services/database_patcher.py         (unchanged)
1254  app/services/validator_service.py        (was 1185, +69, Exp060's migration)
1250  app/services/v6_orchestrator.py          (unchanged — still the highest-risk function, see below)
1173  app/repair/orchestrator.py               (unchanged)
```

**`generate_project_v6` (v6_orchestrator.py) remains the single
highest-risk function in the repo** — 911 lines, cyclomatic complexity
135, the exact function that already produced one confirmed regression
(Exp053→056→057). Nothing this cycle touched its internals; the risk is
unchanged and still the top candidate for a dedicated decomposition
experiment (per Exp059's own backlog item #20).

**New complexity added this cycle, self-assessed honestly:**
`app/services/fix_writer_service.py` more than doubled in size (164→376
lines) from Exp064's semantic-consistency guard. Its most complex new
function, `_collect_basemodel_classes`, measures complexity=20, depth=6
— moderately complex for a freshly-added function. Mitigating factor:
it shipped with 24 dedicated tests (Exp064) and was verified against
115 real files with zero false positives — better-tested than most of
the pre-existing codebase per Part 7's own findings (see
`docs/TEST_QUALITY_REVIEW.md`). Flagged for awareness, not ranked as a
new top-tier risk given the test coverage backing it.

**`run_user_journey`** (`app/runtime/user_journey_runner.py`) remains
the worst raw complexity/depth score in the repo (166/15, per Exp059) —
untouched this cycle, still the top decomposition candidate after
`generate_project_v6`.

---

## Part 4 — State Management Audit

### Global state

Two confirmed module-level mutable containers:
- `app/providers/ai_provider.py:24` — `_provider_cooldown_until: dict = {}`,
  written by `_note_provider_result()` (line 37), read by `_on_cooldown()`
  (line 42). Documented in `CLAUDE.md`. Safe by design — resets on
  process restart.
- `app/utils/cost_tracker.py:34,40` — `_session_calls: list[dict]` and
  `_run_totals: dict`, written by `record_llm_call()`/`record_cache_hit()`,
  cleared by `reset_session()`/`flush_to_log()`. **This is the highest-risk
  state finding this cycle — see below.**

No `@lru_cache`/`@cache` anywhere in `app/` — the project's disk-backed
`llm_cache` is file-based, not an in-memory Python cache, so this
category is otherwise clean.

### Singleton usage

One confirmed, correctly-scoped singleton: `app/queue/dispatcher.py:220`
(`dispatcher = WorkerDispatcher()`), managing live worker subprocess
PIDs. Not a risk — there should be exactly one per running server
process, and the queue path already uses separate OS processes for job
isolation (confirmed via the module's own docstring), unlike the risk
below.

### Environment variables

At least 10 `FORGE_*` variables confirmed via grep, every one with a
sensible default (`FORGE_MAX_STALLED_FIX_ATTEMPTS`, `FORGE_DEPLOY_THRESHOLD`,
`FORGE_CONTRACT_CHECK`, `FORGE_PIPELINE_VERSION`, `FORGE_LLM_CACHE`,
`FORGE_THEMED_SCAFFOLD`, `FORGE_MODEL_DRIVEN_SCHEMA`, `FORGE_SELF_HEAL`,
+2 more). No unhandled-crash-on-missing-var risk found. All non-`FORGE_`
API keys (`GEMINI_API_KEY` etc.) use safe `os.getenv(...)`, never bare
`os.environ["X"]` indexing.

### Hidden coupling from state — ranked, highest-value finding of this Part

1. **[HIGH, confirmed] `cost_tracker.py`'s module globals are shared
   across concurrent `/project/v15` requests.** `main.py:1210`
   (`def project_v15(request: V15Request):`) is a **synchronous** FastAPI
   route — Starlette runs sync `def` routes in a background threadpool,
   not the async event loop, meaning two concurrent HTTP requests to
   this endpoint run on **different threads within the same process**,
   both reading/writing the same module-level `_session_calls`/
   `_run_totals`. Contrast: the job-queue path (`app.queue.worker`) uses
   **separate OS processes** for isolation (confirmed via that module's
   own docstring) — so queue-driven generations are safe; this risk is
   specific to the direct, synchronous `/project/v15` endpoint. Two
   concurrent calls to it could cross-contaminate cost/token totals, and
   one generation's `flush_to_log()`/session-clear could wipe data a
   concurrent generation was still accumulating. **Scope caveat, stated
   honestly**: whether this is exploitable in practice depends on
   deployment configuration (single vs. multi-worker uvicorn) not
   visible in the code itself — flagged as `Unknown`, worth confirming
   in a future cycle, not asserted as definitely live in production.
2. **[MEDIUM, confirmed] `FORGE_DEPLOY_THRESHOLD` independently defined
   in two files** — `app/core/context.py:283` and `app/scoring/engine.py:28`,
   identical pattern, identical default (95), but neither imports the
   other's constant. A future change to one default without the other
   creates silent divergence between deploy-gating and scoring logic
   invisible from either file's own imports.
3. **[LOW, by design] `_provider_cooldown_until`** shared across every
   concurrent job — arguably correct (a 402 means the account is
   globally out of credits), not a defect, noted for completeness.

---

## Part 6 — Documentation Coverage (quantitative)

**Docstring coverage** (public functions/classes, `_`-prefixed excluded,
measured via AST):

| Directory | Documented / Total | % |
|---|---|---|
| `app/repair/` | 9/13 | 69.2% |
| `app/services/` | 119/226 | 52.7% |
| `app/providers/` | 8/18 | 44.4% |
| `app/verification/` | 4/12 | 33.3% |
| `app/runtime/` | 10/39 | 25.6% |
| **Overall** | **150/308** | **48.7%** |

`app/runtime/` and `app/verification/` are the weakest-documented
directories — notably, these overlap exactly with the two highest-risk
modules found elsewhere in this audit (`run_user_journey`'s complexity,
`engine.py`'s Exp060 extension and Part 3's confirmed subprocess-leak
finding). Risk and documentation coverage are inversely correlated here.

**Subsystem docs** (cross-referenced against `docs/*.md`, 29 files):
Repair (6 dedicated docs) and Validators (3 dedicated docs) are
well-covered. **Generator docs: none dedicated** — `docs/FORGEAI_VNEXT_REPORT.md`
describes a *proposed redesign*, not the current live V6 pipeline;
only `CLAUDE.md`'s 5-bullet summary covers what actually runs today, no
diagram. Deployment has one substantive doc
(`docs/V16_DEPLOYMENT_RELIABILITY_AUDIT.md`) whose "V16" title suggests
possible staleness relative to current code — not re-verified this
cycle.

**Developer onboarding**: `CLAUDE.md` (255 lines) is reference/rules-oriented
(env setup, key file pointers, tool-usage rules, a completion checklist)
— not a narrative "here's how the pieces fit together, here's a first
change to try" walkthrough. Present: the reference material. Absent: an
actual codebase tour.

**Architecture diagrams**: exactly **2 of 29** doc files contain a
mermaid diagram (`docs/REPAIR_ARCHITECTURE.md`, `docs/REPAIR_GRAPH.md`)
— unchanged since Exp059, confirming no diagrams were added in
Exp060-064. 13+ of ~15 major subsystems (generation, validation,
verification, scoring, retry, deployment, queue, memory/telemetry,
Observatory, providers, contract, confidence engine, design, knowledge)
have zero visual representation anywhere.
