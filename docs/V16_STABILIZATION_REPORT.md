# ForgeAI V16 Stabilization Report

**Date**: 2026-07-07
**Scope**: Experiments 001-020, commits spanning the V16 reliability-optimization arc through ADR-002.
**Purpose**: a clean baseline before the next phase of work — what changed, why, what evidence supports each decision, and what deterministic bottlenecks remain.

---

## 1. Executive Summary

V16 began as a scattershot bug-fixing effort and converged, over ~20 experiments, on a single repeatable methodology and one architectural principle:

> **Whenever a reliability problem can be solved by replacing AI agreement with deterministic infrastructure, prefer the deterministic solution.**

Two architectural decisions (ADR-001, ADR-002) now embody that principle directly in the pipeline. Every other change in this arc was either infrastructure that made the *evidence* for those decisions trustworthy (telemetry, validation, repair-loop fixes) or a smaller deterministic fix in the same spirit.

---

## 2. Infrastructure Improvements

These fixes didn't change generation behavior — they made every later experiment's evidence trustworthy in the first place.

| Fix | Experiment | Problem | Effect |
|---|---|---|---|
| `generation_log.jsonl` silently broken | 003 | `getattr` on a method (not property), swallowed by bare `except: pass` | V15 had produced **zero real telemetry since 2026-06-28** — every prior confidence-engine decision that cycle ran on stale data. Fixed for $0. |
| Silent-exception audit | 004 | Codebase-wide sweep for other bare `except` blocks hiding real failures | Found and fixed the confidence engine's attribute names (`best_score`/`scores`/`attempt_count` — none of which existed on the real object), which meant deployment-confidence scoring had never actually worked. |
| Filename sanitization | 006 | An endpoint path like `/tasks?limit=5&...` was used as a literal filename for a generated module — an invalid Python identifier | Root-caused via `GenerationContext` audit; fixed deterministically. |
| Preflight config-patcher fix | 009 | Instance-vs-class and case-sensitivity bugs in the deterministic config patcher | First of several "the fix already exists, it's just broken" findings. |
| DB-init / runtime-hang fixes (pre-020, referenced in memory) | — | Unread stdout/stderr pipes filled and hung the backend after a 500; schema created on the wrong engine | Both fixed with drain threads / lazy `get_db` creation — infrastructure that every later canary run depends on staying up. |

**Pattern**: nearly every early V16 experiment was "the mechanism to fix this already existed in the code — it was silently defeated by an unrelated bug." Confirmed independently in ADR-001 (schema generation) and this report's own Section 4.

## 3. Telemetry Improvements

- **`generation_log.jsonl`** restored (Experiment 003) — the single most consequential infrastructure fix this arc, since every downstream decision ("is this a regression or generation variance?") depends on it.
- **Confidence engine** attribute-name fixes (Experiment 004) — deployment-confidence scoring went from silently-broken to functional.
- **Journey-runner status-code fixes** (Experiments 015, 016) — the journey runner was recording `"?"` instead of real status codes, and separately reporting a false "passed" on a 422-retry that itself 500'd. Both fixed; both were corrupting the very CRUD-success signal every canary comparison in this report relies on.
- **ADR-002's own telemetry** (Task 5 of the deterministic-seed-generator plan): `JourneyResult.seed_summary` and codegen-time `generate()` telemetry — the newest addition, and the one that made ADR-002's own validation evidence directly checkable (Section 4) rather than inferred.

**Pattern**: roughly a third of this arc's effort went into making the *measurement instruments* trustworthy before trusting what they measured. This is not overhead — every experiment after Experiment 004 stands on this foundation.

## 4. Validation Improvements

- **Requiredness refinement** (Experiments 012, 013): the NOT-NULL model/schema gap fix originally checked *field presence*; refined to check *schema-field requiredness* instead, closing an incomplete case identified during Experiment 012's own review.
- **`BaseModel`-Query-param fix** (Experiment 011).
- **Frontend missing-import scaffolder wiring** (Experiment 014) — an existing scaffolder that wasn't actually wired into the preflight repair path.
- **Controlled A/B methodology** (Experiment 008): the first time this project used a controlled AppContract-ON-vs-OFF comparison rather than a single before/after run — established the "isolate the variable, don't just eyeball the score" discipline that every experiment since has followed, most visibly in ADR-002's five-line-of-evidence validation (Section 6).

## 5. Repair-Loop Improvements

- **JourneyCRUDFailure 422-coercion fix** (Experiment 010) — Pydantic v2 type-mismatch retries now coerce guessed values into the type the error actually specifies, rather than failing the same way repeatedly.
- **Journey-runner 422-retry false-positive fix** (Experiment 016) — closed a case where a retry that itself 500'd was still recorded as "passed."
- **SignupPage bug fix** (Experiment referenced in `feedback_v16stable_roadmap` memory) — part of the user's explicit ordering (requiredness fix → SignupPage bug → only then AppContract), completed before the model-driven-schema work began.

## 6. ADR-001 — Model-Driven Schema Generation

**Problem**: model↔schema field-name drift (the clearest case: `simple_crm`'s `Contact.name` vs. `ContactCreate.first_name`/`last_name`) — the VNext architecture report estimated this class of cross-file disagreement at **58% of all historical failure instances**, the single largest recurring failure category in the system.

**Decision**: schema generation derives from the *actual generated model*, not an independent re-derivation from the architect description. New reusable module `entity_metadata.py` parses real `Column(...)` definitions; Wave 3 resolves the real model (never a filename guess, never a contentless re-export shim) and injects a binding field contract into the schema prompt.

**Result**: two canaries (Experiments 017, 018). Experiment 018 (clean, no code changes before the run): **CANARY PASSED** — `crm` 91.4 (A) and `blog_cms` 90.3 (A), both full CRUD 11/11, for the first time all session. The reactive `field_patcher` that used to paper over this exact bug did not fire — the field was correct from the first generation attempt.

**Status**: `FORGE_MODEL_DRIVEN_SCHEMA` defaults to `True` (rollback flag preserved, unused).

## 7. ADR-002 — Deterministic Reference-Data Generation

**Problem**: the zero-insert `seed_routes.py` fallback stub, which fires whenever the LLM omits the file (a deliberate no-LLM-call decision, since the LLM has no project context in that repair branch). Permanently blocked FK-validated Create calls whenever it fired — the residual blocker Experiment 019 surfaced after fixing seed *timing*.

**Decision**: extend ADR-001's `entity_metadata.py` reuse pattern to seeding. FK-target candidacy + transitive required-FK eligibility (a candidate is seedable only if every required FK it declares also points to another eligible entity — computed as a fixed point, with required self-references excluded unconditionally), Kahn's-algorithm topological ordering with explicit cycle detection, count-based idempotency (`count() >= 3`, not field-guessing or `UNIQUE`-constraint reliance), purely type-driven value generation, and a single fallback boundary that never lets this feature make generation fail.

**Built via Subagent-Driven Development**: 7-task TDD plan, fresh implementer + independent reviewer per task, two fix-and-re-review rounds (a duplicated helper function; a telemetry gap) caught and closed before merge — commits `a609753..1f3e893`.

**Validated on five independent lines of evidence** (not one benchmark):
1. 35 passing unit/execution tests (including tests that `exec` rendered source against a real in-memory SQLite database).
2. Clean local validation, zero regressions, across all 5 implementation tasks.
3. Two live canary firings, both independently confirmed correct via telemetry (todo: 3 real rows inserted; blog_cms: correctly excluded a business entity requiring a real user).
4. **Targeted deterministic verification** — the strongest evidence: a real generated project, one unrelated confound neutralized, the fallback path forced on demand, a real server driven with real HTTP requests. `POST /seed` → 3 rows; `POST /tasks` against the seeded reference → `201 Created`.
5. Negative evidence: a second canary where the fallback never fired still showed the same unrelated `blog_cms` regression — ruling out this feature as its cause.

**Status**: Accepted. Full detail: `docs/adr/ADR-002-deterministic-reference-data-generation.md`, `experiments.md` Experiment 020.

## 8. Benchmark Evolution

Full canary history (`forge_score`, todo / blog_cms / crm):

| Run | todo | blog_cms | crm | Note |
|---|---|---|---|---|
| m0-quick-wins | 76.9 | 33.0 | 76.9 | baseline |
| m1-contract | 25.3 | 31.5 | 0.0 | provider exhaustion, inconclusive |
| m1-contract-gemini | 25.5 | 66.1 | 65.8 | clean run, real signal |
| m1-post-filename-fix | 76.4 | 94.1 | 72.6 | |
| m1-contract-OFF-control | 76.9 | 45.0 | 47.1 | A/B control |
| m1-config-patcher-fix | 76.4 | 34.3 | 66.6 | |
| m1-journey-crud-fix | 99.3 | 67.5 | 44.4 | |
| m1-querybasemodel-fix | 99.3 | 93.3 | 65.8 | |
| m1-notnullgap-fix | 76.4 | 34.3 | 72.6 | |
| m1-notnullgap-requiredness | 67.4 | 86.2 | 74.3 | |
| m1-signuppage-fix | 73.9 | 87.4 | 66.9 | |
| m1-status-code-fix | 73.9 | 68.3 | 0.0 | |
| m1-retry500-fix | 73.9 | 84.8 | 66.2 | |
| m1-model-driven-schema | 73.9 | 61.5 | 39.4 | confounded (unrelated crashes) |
| m1-model-driven-schema-confirm | 73.9 | **90.3** | **91.4** | clean confirming run — ADR-001 |
| m1-seed-before-crud | 73.9 | 90.3 | **91.6** | crm 0 fix attempts, best yet |
| adr002-deterministic-seeder | 76.0 | 87.3 | 88.6 | ADR-002 fired 2x, correct both times |
| adr002-deterministic-seeder-confirm | 76.0 | 83.3 | 87.5 | ADR-002 fired 0x — exonerating for blog_cms drift |

**Reading this honestly**: forge_score is not monotonically increasing, and it shouldn't be treated as if it should be — this report's own Section 6-7 evidence shows *why*: individual runs are confounded by independent LLM-generation variance (a malformed constructor here, an endpoint-naming mismatch there) that no single architectural fix controls. The project's own discipline — judging experiments by root-cause-traced telemetry, not aggregate score alone — is what makes ADR-001 and ADR-002 defensible despite noisy surrounding numbers.

## 9. Engineering Metrics

- **316 total commits** in this repository as of this report.
- **20 experiments** logged in `experiments.md`, each with a stated hypothesis, evidence, and honest verdict (including at least one explicitly marked INCONCLUSIVE — Experiment 002 — and several explicitly marked "confounded, unrelated to the change under test").
- **2 Architecture Decision Records** (ADR-001, ADR-002), each with a Status/Context/Decision/Result/Consequences/Non-Goals structure and a documented rollback strategy.
- **ADR-002 specifically**: 1 frozen design spec (multi-round architecture review before implementation), 1 implementation plan (7 tasks), 35 unit/execution tests, 2 canary runs, 1 targeted deterministic verification — built end-to-end via Subagent-Driven Development with independent task-scoped review gates.
- **Cost discipline**: every experiment this arc reports its actual $ cost; several (003, 004, 006) are explicitly "$0, Claude-only" fixes with no LLM generation spend at all.

## 10. Remaining Deterministic Bottlenecks

Identified directly in this arc's own evidence, not speculation:

1. **Endpoint/routing-naming drift** (surfaced by ADR-002's own canary, Experiment 020): frontend calls to `GET /articles?author_id=`, `GET /authors/${userId}`, `POST /articles` with no matching backend route (`post_routes.py`/`article_routes.py` naming mismatch). This is architecturally the same *class* of cross-file disagreement ADR-001 solved for model↔schema — a smaller-scoped "AppContract for routes/endpoints" is the natural next candidate, and is now a smaller scope than originally estimated since ADR-001 already closed the model↔schema slice.
2. **Malformed route-handler constructors** (todo's `task_in.items().model_dump()` bug, Experiment 020): an LLM-generation-quality defect, not an architectural gap — a candidate for a deterministic route-handler validator/patcher in the same family as the existing `field_patcher`/`config_patcher`.
3. **Relationship/secondary-table extraction** — flagged as out-of-scope in *both* ADR-001 and ADR-002; `entity_metadata.py` only reads `Column(...)`, not `relationship()`. Blocks full field-name-drift coverage for many-to-many fields (e.g. blog_cms's `tags`) and full lookup-entity coverage for relationship-based reference data.
4. **Frontend import/build reliability** (V16 Phase 4, not yet started this arc).

## 11. Roadmap Toward V16 Stable

Per the user's own phased roadmap, restated with current status:

- ✅ **Phase 1 — Infrastructure stabilization**: done (telemetry, confidence engine, exception audit, runtime hang/DB-init fixes).
- ✅ **Phase 2 — Generation consistency**: done (ADR-001, model-driven schema generation).
- ✅ **Phase 3 — Reference-data reliability**: done (ADR-002, deterministic seeder), with this report's own evidence.
- 🔄 **Phase 4 — Frontend reliability** (imports, components, build): not yet started.
- 🔄 **Phase 5 — Endpoint consistency** (AppContract-lite): scope now smaller than originally estimated, per ADR-001's own "What This Does Not Change" section; the specific defect class is now directly evidenced (Section 10, item 1), not speculative.
- ⬜ **Phase 6 — Release Candidate**: not started.

**This report's recommendation, per the project's own stated engineering philosophy**: before starting Phase 4 or 5, apply the same question that produced ADR-001 and ADR-002 — *"what deterministic bottleneck is costing us the most reliability, and can it be solved by replacing AI agreement with deterministic infrastructure?"* — to the two concrete candidates in Section 10 (routing-naming drift, malformed constructors), rather than starting from the roadmap's phase labels alone. Both are now backed by direct telemetry from this arc's own canaries, not inference.
