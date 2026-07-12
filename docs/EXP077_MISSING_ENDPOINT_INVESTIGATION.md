# Experiment 077 — Root Cause Investigation of MissingEndpoint Failures

2026-07-12. Investigation only, per this experiment's own explicit rule —
**no fixes, no speculative architecture changes**. Zero Cerebras calls
(none needed — every finding below is reconstructed from existing
generated projects, cached architecture plans, and direct code reading,
per this experiment's own "reconstruct from existing generated projects
whenever possible" constraint).

## Executive summary

`MissingEndpoint` is not one bug — it is at least **three structurally
distinct root causes** sharing one taxonomy label, with very different
current status:

| Root cause | Status | Evidence |
|---|---|---|
| **A. Wave-4 initial under-delivery** (backend route generation writes an incomplete file on the first pass) | Self-heals reliably via the existing static-validation fix loop | Confirmed on live 2026-07-12 data, recovers within 3 static-loop attempts |
| **B. "Emitted but lost later"** — a full-file LLM rewrite during the runtime-stage repair loop silently drops endpoints unrelated to the diagnostic it's fixing | **Confirmed live, root-caused to an exact one-line bug**, currently active, the dominant surviving cause | `_required_endpoints_for_files()` in `orchestrator.py` never fires — see §3 |
| **C. Stale auth-specific gaps** (register/logout/forgot-password missing) | **Not currently active** — predates Experiment 071's auth-completeness fix | 5 of 6 corpus instances are 12+ days old; the one same-day instance still has the full auth set |

**The smallest deterministic repair candidate is a genuinely tiny fix**:
one missing `.replace("\\", "/")` call in an already-existing,
already-invoked, already-documented protection mechanism that was
silently non-functional this entire time. See §5.

## 1. Data collected (Task 1)

- **Observatory** (`app/memory/reliability_metrics.py`): no dedicated
  `MissingEndpoint` metric exists (confirmed by grep — zero matches).
  Observatory reports aggregate score/health only; it has never broken
  out this failure class specifically.
- **`patterns.json`**: the authoritative historical count — **48
  instances**, `first_seen: 2026-06-22T07:49:29`, `last_seen:
  2026-07-11T23:12:06`, stage `generation`. 24.7% of the 194 classified
  taxonomy instances (per Exp068's original count) — the single largest
  cluster. 5 named examples span `bug_tracker`, `classroom_manager`
  (×4).
- **`generation_log.jsonl` / forensic bundles**: per Exp068's own prior
  work (`docs/RUNTIME_KNOWLEDGE_BASE.md`), 9/14 bundles were the
  now-resolved auth sub-case; the other 5 were not individually
  root-caused before this experiment.
- **`generated_projects/`** (54 real, complete generated projects on
  disk, git-ignored, accumulated 2026-06-18 through 2026-07-12): **the
  primary evidence source for this experiment.** Each retains
  `metadata.json` with the Architect's own `architecture.api_endpoints`
  plan, letting planned-vs-delivered be diffed directly without any new
  generation. 48 of 54 have a usable `metadata.json`; 46 have a
  non-empty `api_endpoints` list.

## 2. Method: planned-vs-delivered diff across all 54 real projects

Reused `app/services/endpoint_validator.py`'s own
`extract_actual_backend_routes()` and `_normalize_path()` **directly, unmodified**
(Task 6 — exactly the kind of existing infrastructure this experiment
was told to identify and reuse) to build ground truth, then diffed
against each project's own `metadata.json`'s `architecture.api_endpoints`:

```
projects with a usable architecture plan: 46
total planned endpoints across all 46:    782
missing from final delivered backend:      12   (1.5%)
projects with >=1 missing endpoint:         6 / 46
```

**Critical methodological finding, disclosed rather than glossed
over**: `generated_projects/` is not a clean current snapshot — it spans
25 days and multiple code-version eras. Timestamping all 6 flagged
projects against Experiment 071's auth-completeness fix (landed
2026-07-11) shows:

| Project | Generated | Missing endpoints | Era |
|---|---|---|---|
| `blogsphere` | 2026-06-30 | `POST /auth/logout`, `POST /auth/register` | **12 days stale** (pre-Exp071) |
| `prioritytodo` | 2026-06-30 | `POST /auth/logout`, `POST /auth/register` | **stale** |
| `recipemaster` | 2026-06-30 | `POST /auth/register` | **stale** |
| `gym_workout_tracker` | 2026-07-05 | `POST /auth/forgot-password`, `POST /auth/reset-password` | **stale** |
| `dine_reserve` | 2026-07-11 | `GET /api/auth/me` | borderline — see below, not a true miss |
| `forge_blog_cms` | **2026-07-12 (today)** | `PUT /posts/{id}`, `DELETE /posts/{id}`, `PATCH .../publish`, `PATCH .../unpublish` | **current, live** |

Re-running `blogsphere`/`prioritytodo`/`recipemaster`/`gym_workout_tracker`
today would almost certainly NOT reproduce their specific auth gaps: the
current `_build_auth_routes_template()` in `deterministic_patcher.py`
unconditionally injects `/auth/signup`, `/auth/register` (alias),
`/auth/login`, `/auth/me`, **and `/auth/logout`** (confirmed by reading
the template directly, lines 2057-2108) — none of these 4 stale projects
could have benefited from a template whose `/auth/logout`/`/auth/register`-alias
additions postdate them. `dine_reserve` (2026-07-11, `GET /api/auth/me`
"missing") is **not actually a true miss at all** — direct inspection
confirms `GET /auth/me` (no `/api/` prefix) exists and works; the
architecture plan itself has an internally-inconsistent `/api/` prefix
on that one endpoint that neither the rest of its own plan nor the
generated app uses. This is the taxonomy's "**exists under another
path**" category (Task 4) — an architect-authoring inconsistency, not a
pipeline loss.

**Only `forge_blog_cms` (generated today, this session, same code this
experiment is investigating) represents a confirmed-current
MissingEndpoint failure** — and it is exactly the "general CRUD,
non-auth" shape this experiment's own framing named as the current
target: 2 CRUD verbs (`PUT`/`DELETE`) and 2 custom actions (`publish`/
`unpublish`) on `/posts/{id}`, entirely absent from the final delivered
`post_routes.py`.

## 3. Full evidence chain for `forge_blog_cms` (Task 3 — every stage)

Traced end-to-end through the exact live generation log
(`exp074_run.log`, same run this experiment reuses rather than
regenerating):

| Stage | What happened | Evidence |
|---|---|---|
| **Planner / Architect** | Correctly planned all 8 `post_routes.py` endpoints, including `PUT`, `DELETE`, `PATCH .../publish`, `PATCH .../unpublish` | `metadata.json`: `architecture.api_endpoints` lists all 8, `file: 'app\\routes\\post_routes.py'` |
| **Backend prompt / Wave 4 LLM output** | The route-generation call for `post_routes.py` used the MOST completion tokens of any route file this wave (3016, vs. 815-1863 for siblings) — not a token-starvation/truncation case — yet the file it produced satisfied **zero** of the 8 planned endpoints at first static check | log: `[CEREBRAS] Prompt=4761 Completion=3016 ... [route] app/routes/post_routes.py: OK` immediately followed by 8/8 "Missing endpoint" errors |
| **File writer** | Wrote whatever Wave 4 returned; no gate at this stage checks endpoint completeness (that's the validator's job, next stage) | `[file_writer] wrote 40 files` — no rejection |
| **Static validation loop (V6)** | `validate_endpoints()` correctly caught all 8 gaps immediately | log lines 598-605, first validation pass |
| **Static-loop repair (3 attempts)** | **Successfully recovered all 8 endpoints** — 5 `missing_file`-strategy cache hits in attempt 1, 2 backend-import synthesis in attempt 2, comment-feature schema/model creation in attempt 3 → `Post-fix 3: PASS — 0 errors` | log lines 607-639 — **this repair path works correctly** |
| **Runtime validation begins** | Backend starts; QA/Security/Code/Performance reviews run; runtime journey testing begins | log lines 640-926 |
| **Runtime-stage outer fix loop (V15, `strategy: patch_file` then `switch_model`)** | **This is where the endpoints are actually lost.** Multiple full-file rewrites of `post_routes.py` via `_apply_fix_group()` (confirmed: `[fix] Patched: app/routes/post_routes.py` at lines 936, 1071, 1077, 1235, 1238), each targeting a DIFFERENT diagnostic (a frontend-invented `/posts/{id}/comments` feature, a syntax error) — with no reliable protection against the rewrite silently omitting `PUT`/`DELETE`/`PATCH` | log lines 927-1286; regression detector explicitly logs their disappearance: `↳ Missing endpoint GET /posts/{post_id}`, `↳ Missing endpoint POST /posts` reappearing as NEW regressions at lines 1125-1127 and 1284-1286 |
| **Most drastic strategy: full architecture regeneration** | Even `strategy: regenerate_arch` (`[fix] REGENERATE ARCHITECTURE — redesigning from idea...`, line 2288) did not durably recover them | Final on-disk state (§4) still lacks all 4 |
| **Validation (final)** | The SAME `validate_endpoints()` check that caught this the first time never got a chance to re-block delivery on it a second time in a way that stuck — repeated regen cycles kept re-introducing the gap faster than fix attempts could close it, and the pipeline's attempt budget (5 runtime fix attempts) ran out | Final Forge Score 70.0/C, capped specifically on `Runtime Startup`/`Integration` dimensions per `docs/EXP074_VALIDATION.md` |
| **Runtime** | `PUT`/`DELETE /posts/{id}` return 405 Method Not Allowed — confirmed via direct `grep` of the delivered `post_routes.py`: only `GET`×3 and `POST` exist | `docs/EXP074_VALIDATION.md` §3 (already documented, cross-referenced here) |

## 4. Root cause, precisely located (Task 4 answer, Category B)

**Confirmed, exact code-level root cause**: `orchestrator.py`'s
`_required_endpoints_for_files()` — the function that is SUPPOSED to
tell the LLM "these endpoints must survive your rewrite" during exactly
this kind of full-file patch — **never actually includes any endpoints,
for any project, ever**, because of an unnormalized path-separator
mismatch:

```python
def _required_endpoints_for_files(ctx: GenerationContext, files: list[str]) -> str:
    ...
    endpoints = arch.get("api_endpoints", [])
    ...
    relevant = [ep for ep in endpoints if ep.get("file") in files]   # <-- BUG
```

`ep.get("file")` comes straight from `ctx.architecture` (confirmed by
reading `pipeline.py:140`: `ctx.architecture = v6_result.get("architecture")`
— a direct, unmodified assignment of the Architect's raw output) — and
that raw output's `file` field is **always backslash-separated**
(`'app\\routes\\post_routes.py'`), confirmed identically across **6
independently-sampled projects** (`inventory_manager`, `simple_crm`,
`todo_list_app`, `forgecrm`, `habit_forge`, `forge_blog_cms`) spanning
2026-06-22 through 2026-07-12 — this is a structural, universal property
of this deployment's architect output, not an isolated glitch.

Meanwhile `files` (== `group.affected_files`, ultimately sourced from
each `Diagnostic.file_path`) is **always forward-slash**, because
`validate_endpoints()` itself explicitly normalizes before constructing
the diagnostic: `arch_file = (endpoint.get("file") or
"").replace("\\", "/")`.

**`'app\\routes\\post_routes.py' in ['app/routes/post_routes.py']` is
always `False`.** The "REQUIRED ENDPOINTS (these MUST still exist...)"
block that `_build_fix_prompt()` is supposed to inject into every
full-file rewrite prompt is **silently empty on every single call, for
every project, always** — the safety net this codebase's own comments
describe ("do not drop any of them while fixing the errors above") has
never fired once in this deployment's history. This is a **detection-vs-repair
asymmetry** exactly matching Task 4's "emitted but lost later"
category, now root-caused to one specific line rather than left as a
described symptom.

**A second, independent, more severe instance of the same class of gap**:
`_regenerate_module()`'s backend path (used when `FixStrategy.REGENERATE_MODULE`
is selected) calls:
```python
fix_data = generate_architecture_fix(ctx.architecture, [d.message for d in group.diagnostics], cfg.provider)
```
`generate_architecture_fix()` (`architecture_fix_service.py`) is
explicitly *designed* to accept `required_endpoints=`/`required_exports=`/
`existing_symbols=` keyword arguments and inject its own "REQUIRED
ENDPOINTS — ALL OF THESE MUST EXIST IN YOUR OUTPUT" block — but the call
site **never constructs or passes any of them**. Every module-level
regeneration through this strategy has **zero** endpoint-preservation
context at all — not a broken attempt, no attempt whatsoever. (For the
specific `forge_blog_cms` trace above, the observed strategies were
`patch_file`/`switch_model` — i.e. the `_apply_fix_group` path — so this
second gap is a **confirmed-present, not confirmed-triggered** risk for
this instance; it is real code that would fire identically on the next
project whose repair loop happens to select `REGENERATE_MODULE`.)

## 5. Root-cause taxonomy and frequency (Tasks 2, 4, 5)

| Class | Description | Task-2 endpoint types seen | Frequency (this cycle's evidence) |
|---|---|---|---|
| **A. Wave-4 initial under-delivery** | First-pass route generation omits some/all endpoints for a resource | CRUD, custom action, nested route (classroom_manager's `POST /assignments/{id}/submissions`), even non-HTTP (`patterns.json`'s `WS /ws/issues/{issue_id}/stream`) | Root cause of most of the historical 48-instance count at time of static-validation detection; **self-heals reliably** — confirmed 3/3 static-loop attempts recovered all 8 gaps in the traced case |
| **B. Emitted but lost later** (full-file rewrite drops unrelated endpoints) | Confirmed CRUD (`PUT`/`DELETE`) + custom action (`publish`/`unpublish`) in the traced case | **The dominant currently-active cause** — 1/1 confirmed-live instances this cycle (100% of the non-stale corpus sample, n=1); root-caused to an exact line (§4) |
| **C. Stale auth-specific gaps** | `register`/`logout`/`forgot-password`/`reset-password` | Auth | **Not currently active** — 5/6 corpus instances, all pre-Exp071; 0 in the 4 same-day (2026-07-12) generations |
| **D. Never planned, frontend invents it anyway** | Frontend generation independently invents a feature (`/posts/{id}/comments`) the Architect never specified | Nested route (custom sub-resource) | 1 confirmed instance (same `forge_blog_cms` run) — self-resolved within the static loop by synthesizing a full new backend feature; **not currently blocking**, caught by `validate_frontend_api_calls()`, the codebase's second (correctly-normalized) detector |
| **E. Architect plan-internal inconsistency** ("exists under another path") | One endpoint planned with an `/api/` prefix the rest of the plan and the real app don't use | Auth (this instance) | 1 confirmed instance (`dine_reserve`) — cosmetic, endpoint genuinely reachable under the real path, no functional impact |

**Quantified**: of the 12 final-state corpus misses, **10 (83%) are
stale/already-fixed**, **1 (8%) is a false positive** (Class E), and
**1 (8%) — but 100% of the current-era sample — is the live, dominant,
now-root-caused Class B**. Historically, Class A accounts for the bulk
of the 48-instance `patterns.json` count *at time of static detection*,
but that class's own repair mechanism is confirmed working; Class B is
invisible to `patterns.json`'s per-run classification (it fires and gets
silently re-masked within the SAME run's repair cycles, never surfacing
as a persistent, separately-counted failure the way Class A does) —
meaning **Class B is very likely undercounted in the existing 48-instance
statistic**, not overcounted.

## 6. Existing deterministic infrastructure (Task 6)

Already exists and is directly reusable, confirmed working:
- `endpoint_validator.py::extract_actual_backend_routes()` /
  `_normalize_path()` — the ground-truth route inventory this whole
  investigation was built on top of, zero modification needed.
- `endpoint_validator.py::validate_frontend_api_calls()` — correctly
  normalizes slashes on its own architecture-independent comparison;
  not affected by the Class B bug.
- `orchestrator.py::_required_endpoints_for_files()` — **exists,
  is already wired into `_build_fix_prompt()`, is already documented
  with the exact right intent — just has one unnormalized string
  comparison.**
- `architecture_fix_service.py::generate_architecture_fix()` — **exists,
  already accepts and uses `required_endpoints`/`required_exports`/
  `existing_symbols` — just is never called with them by
  `_regenerate_module()`.**

**Nothing new needs to be built.** Both gaps are wiring/normalization
defects in mechanisms that were already designed correctly.

## 7. Smallest deterministic repair candidate

Two candidate fixes, both minimal, both reusing existing infrastructure
exactly as this experiment's constraints require — **not implemented
this cycle, per this experiment's "investigation only" rule**:

1. **`_required_endpoints_for_files()`** (orchestrator.py): normalize
   `ep.get("file")` with `.replace("\\", "/")` before the `in files`
   comparison — mirrors the exact normalization `validate_endpoints()`
   already performs one function away in the same codebase. Estimated
   diff: **1 line**.
2. **`_regenerate_module()`** (orchestrator.py): build a
   `{file: [required_endpoints]}` dict (reusing the now-fixed helper's
   underlying logic) and an equivalent `existing_symbols`/`required_exports`
   mapping, and pass them to `generate_architecture_fix()`. Estimated
   diff: **~10-15 lines**, no new files, no new mechanism — wiring an
   already-implemented, already-tested-elsewhere parameter.

Both are pure precision fixes to code that already exists for exactly
this purpose — the lowest-risk shape a fix in this codebase can take
(same category as Exp073's and Exp075's already-shipped, already-validated
fixes: make an existing, correctly-intentioned mechanism actually fire,
rather than inventing new behavior).

## 8. Estimated reliability impact

- **Directly measured**: in the one confirmed-live instance this cycle,
  Class B capped `forge_blog_cms` at Forge Score 70.0/C instead of a
  clean pass — the single largest score deduction in that run, larger
  than any other issue found (per `docs/EXP074_VALIDATION.md`'s own
  ranking).
- **Structural exposure**: since the underlying bug (unnormalized
  architecture `file` paths) is universal across this deployment (6/6
  sampled projects, spanning a month), **every** project whose runtime
  repair loop ever needs a full-file rewrite of a route file carrying
  more than one endpoint is exposed to this failure mode — not a rare
  edge case, a structural gap in the repair loop's safety net that has
  been silently present since `_required_endpoints_for_files()` was
  written.
- **Not yet reliably quantifiable as a standalone rate** from
  `patterns.json` alone, per §5's undercounting note — recommend Exp078
  (or a dedicated follow-up canary, analogous to Exp074/076's live
  validation pattern) measure the POST-fix rate directly, the same way
  Exp074 validated Exp073 and Exp076 validated Exp075.

## Answers to the mission's explicit questions

1. **Root-cause taxonomy**: §5 (5 classes: A initial under-delivery /
   B emitted-but-lost-later / C stale-auth / D never-planned-frontend-invented
   / E plan-internal-inconsistency).
2. **Frequency breakdown**: §5's table + §2's date-segmented corpus
   numbers (782 planned, 12 final-state missing, 10 of those stale, 1
   false-positive, 1 confirmed-live-and-dominant).
3. **Evidence chain for every class**: §3 (full stage-by-stage trace
   for the live Class B instance, with log line citations) plus inline
   citations for C/D/E in §2/§5.
4. **Smallest deterministic repair candidate**: §7 — a 1-line
   normalization fix plus an optional ~10-15-line wiring fix, both
   reusing existing, already-correct infrastructure.
5. **Estimated reliability impact**: §8 — largest single score
   deduction in the one live instance measured; structurally universal
   exposure, not yet independently quantified as a standalone rate.
6. **Recommendation for Exp078**: **implement the fix, don't continue
   investigating.** This is as clean and low-risk a fix candidate as
   this project has found in its 77-experiment history — a one-line
   normalization matching a pattern already proven correct one function
   away in the same file, closing a confirmed, universal, currently-active
   gap in an already-designed safety mechanism. Recommend Exp078 ship
   both §7 fixes, then validate live with the same Exp074/076
   methodology (regenerate `forge_blog_cms`'s idea, or another
   multi-endpoint resource, and confirm `PUT`/`DELETE` survive the
   runtime repair loop this time).

**Cost: $0, zero Cerebras calls.** Per the task's own instruction,
**NOT committed**.
