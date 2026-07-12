# Experiment 090 — Post-Stabilization Reliability Assessment

2026-07-13. Investigation only, $0, zero Cerebras calls. Method: fresh
`scripts/failure_report.py` run, full `patterns.json` (207 all-time
instances)/`generation_log.jsonl` (101 entries, back to 2026-06-28)
re-scan, `canary_history.json`'s full 40-run history — no new
generation.

## 1. Updated failure taxonomy — closed vs. active

| Class | All-time | Last-30 | Last seen | Status |
|---|---|---|---|---|
| MissingEndpoint | 48 (23.2%) | 0 | 07-11 | **CLOSED** — Exp077–082 |
| JourneyCRUDFailure | 32 (15.5%) | 3 | 07-12 | **ACTIVE — #1 remaining** |
| AttributeError | 22 (10.6%) | 11 → **2** post-filter | 07-12 | **MOSTLY CLOSED** — 9/11 last-30 were the now-fixed SignupRequest.username shape (Exp083–086); 2 residual (new sub-shape, see §3) |
| ImportError | 14 (6.8%) | ~2 est. | 07-12 | ACTIVE, model-quality |
| ConfigAttributeError | 13 (6.3%) | 0 | 07-07 | **CLOSED** (pre-Exp048 cycle) |
| SQLAlchemyError | 11 (5.3%) | 0 | 07-06 | STALE, likely closed |
| ModuleNotFoundError | 10 (4.8%) | ~1 est. | 07-12 | ACTIVE, model-quality |
| RouterExportMismatch | 9 (4.3%) | 0 | 07-06 | **CLOSED** — Exp021 |
| SyntaxError | 8 (3.9%) | ~1 est. | 07-12 | RESIDUAL — Exp049 fixed dominant cause |
| FrontendBuildError | 7 (3.4%) | 0 | 06-30 | **CLOSED** — Exp049 |
| NoReferencedTableError | 6 (2.9%) | 0 | 06-27 | STALE |
| **PydanticSerializationError** | 6 (2.9%) | 2 | 07-12 | **CLOSED** — Exp087–089; both last-30 occurrences predate the fix, 2 independent live runs since confirm zero recurrence |
| NotNullViolationError | 5 (2.4%) | ~1 est. | 07-12 | ACTIVE — CREATE-path only (UPDATE-path closed, Exp075/076); overlaps JourneyCRUDFailure §3 |
| ValidationError | 3 (1.4%) | 0 | 07-01 | STALE |
| RelationshipModelNotImported | 3 (1.4%) | 0 | 06-22 | STALE |
| TimestampNotNullError | 2 (1.0%) | ~0 | 07-12 | ACTIVE, negligible volume |
| ResponseValidationError | 2 (1.0%) | 0 | 07-06 | STALE |
| 6 singleton classes | 1 each | 0 | ≤07-01 | STALE, negligible |

**Threads named in this experiment's context, mapped to taxonomy
effect**: AST attribute repair (Exp073) fixed a repair-*quality* bug
(file-wide over-correction), not a standalone taxonomy bucket. NOT NULL
UPDATE semantics (Exp075/076) closed the UPDATE-path share of
`NotNullViolationError`/`TimestampNotNullError` — the CREATE-path share
remains open, folded into JourneyCRUDFailure below. Runtime endpoint
preservation (Exp077–082) closed `MissingEndpoint` outright. Retry
strategy generation memory (Exp080–082) is infrastructure that makes the
endpoint-preservation fix reachable, not its own taxonomy bucket. Cross-
file auth field validation (Exp083–086) closed the dominant share of
`AttributeError`. ORM dictionary serialization (Exp087–089) closed
`PydanticSerializationError`.

## 2. Measured by category (Task 3)

- **Remaining deterministic failures** (a specific, fixable code
  pattern, not LLM randomness): JourneyCRUDFailure's two sub-shapes
  (below), and a newly-surfaced seed-script field-mismatch tail (§3).
- **Remaining model-quality failures** (vary per generation, no single
  fixable root cause): `ImportError`, `ModuleNotFoundError`, residual
  `SyntaxError` edge cases beyond Exp049's dominant shape.
- **Runtime failures**: nearly everything active is `stage=runtime`
  except residual `SyntaxError` (`build`).
- **Repair-loop failures** — the most important finding this cycle:
  **JourneyCRUDFailure has a confirmed 0% same-run self-heal rate**
  across all 23 tracked non-resolving instances (`fix_count` 2–5,
  never fixed by the end of the run — checked directly, not inferred).
  This is a materially different signal than most other classes, where
  the repair loop at least sometimes recovers. It means the existing
  LLM-driven repair loop has a *proven, not assumed*, track record of
  failing to fix this specific pattern no matter how many attempts it
  gets — the strongest argument in this whole report for a deterministic
  (pre-runtime) fix over continuing to rely on repair.

## 3. Deep-dive: JourneyCRUDFailure, the #1 remaining active class

Two distinct, recurring sub-shapes across the 23 tracked instances:

- **"Create entity: 40x/50x"** (majority of instances): the same
  ownership-FK-not-injected-from-auth family already directly observed
  live in Exp079/082/086/089's own canary runs (`IntegrityError: NOT
  NULL constraint failed: posts.author_id` — the handler doesn't set
  `obj.author_id = current_user.id` before `db.add()`). Deterministic in
  shape; NOT NULL UPDATE semantics (Exp075/076) only covered the
  UPDATE path, not CREATE.
- **"Edit entity: 405" / "no entity_id captured"**: a distinct shape not
  previously root-caused by name in this experiment series — most
  consistent with a route registration/HTTP-method mismatch (405 Method
  Not Allowed means the path exists but no handler matches the verb
  used) or an ID not being returned/captured from the create step.
  Occurred as recently as today (07-12, `inventory management system`
  and `blog CMS` ideas).

Both sub-shapes recur across multiple, unrelated app categories (todo,
CRM, blog, inventory) — a general pattern, not app-specific, matching
this whole experiment series' repeated finding that the highest-impact
bugs are systemic LLM-generation habits, not one-off mistakes.

## 4. New finding: seed-script AttributeError tail

Filtering the last-30 `AttributeError` count (11) by the now-closed
`SignupRequest.username` shape (9) leaves exactly 2 residual instances,
both co-occurring with `POST /seed returned 500`:

```
AttributeError: type object 'User' has no attribute 'name'
AttributeError: 'UserCreate' object has no attribute 'password'
```

Both happen inside the demo-data seeding path specifically, not the
user-facing CRUD/auth endpoints — a narrower, previously-unflagged
sub-shape, low volume (2/30) but worth naming for a future cycle rather
than left unlabeled inside the generic `AttributeError` bucket.

## 5. Ranked table — Impact = Frequency × Severity

Severity 1–5 (5 = total/unrecoverable crash, 4 = blocks a core workflow,
3 = degrades UX, 2 = minor, 1 = cosmetic). Frequency = last-30 count
where meaningful, all-time where last-30 is too sparse to rank reliably.

| Class | Frequency | Severity | Impact | Determinism |
|---|---|---|---|---|
| **JourneyCRUDFailure** | 3 (last-30), 23 non-self-healing all-time | 4 (blocks core CRUD) | **12–92** | Deterministic (both sub-shapes) |
| ImportError | 14 (all-time; last-30 too sparse to isolate) | 4 (startup/handler crash) | ~56 | Model-quality |
| ModuleNotFoundError | 10 (all-time) | 5 (startup crash) | ~50 | Model-quality |
| NotNullViolationError (CREATE-path) | 5 (all-time) | 4 | ~20 | Deterministic — same family as JourneyCRUDFailure §3 |
| SyntaxError (residual) | 8 (all-time) | 5 (build crash) | ~40 (declining — dominant shape already fixed) | Model-quality edge cases |
| Seed-script AttributeError | 2 (last-30) | 3 (breaks demo data, not core flow) | 6 | Deterministic (new, §4) |
| TimestampNotNullError | 2 (all-time) | 3 | 6 | Deterministic, negligible volume |

JourneyCRUDFailure tops the ranking on any reasonable reading — highest
severity among active classes, highest confirmed persistence (0%
self-heal), and recurs across the widest spread of app categories.

## 6. Estimated cumulative reliability improvement since Exp048

Using `generation_log.jsonl`'s own 101-entry history, split at Exp048's
ship date (2026-07-11), same metric definitions the live dashboard uses:

| | Before Exp048 (n=64) | On/after Exp048 (n=37) | Δ |
|---|---|---|---|
| Generation success | 31.2% | 45.9% | **+14.7 pts** |
| First-try (0 fixes) | 25.0% | 29.7% | **+4.7 pts** |
| Avg forge score | 75.1 | 83.6 | **+8.5 pts** |

Wider view (oldest 30 vs. newest 30 entries, spanning the full
2026-06-28→07-12 history, pre-dating Exp048 by two weeks): generation
success 13.3% → 43.3% (**+30 pts**), first-try 6.7% → 26.7% (**+20
pts**), avg score 70.8 → 81.6 (**+10.8 pts**). The since-Exp048 window is
the more precisely-scoped answer to this task; the wider window shows
the full arc this entire experiment series has covered.

## 7. Top 10 Remaining Reliability Risks

1. **JourneyCRUDFailure — Create-path (ownership-FK not injected)** —
   highest confirmed impact, deterministic, 0% self-heal.
2. **JourneyCRUDFailure — Edit-path (405 / no-entity-id)** — same
   bucket, distinct root cause, not yet characterized in depth.
3. **ImportError** — model-quality, second-highest all-time volume among
   active classes.
4. **ModuleNotFoundError** — model-quality, startup-crash severity.
5. **SyntaxError (residual)** — mostly closed (Exp049), edge cases
   remain, model-quality-driven.
6. **NotNullViolationError (CREATE-path)** — same family as #1, likely
   fixable together.
7. **Seed-script AttributeError** — newly named this cycle, low volume,
   deterministic once investigated.
8. **TimestampNotNullError** — negligible volume, same family as #6.
9. **Stale classes' recurrence risk** (`SQLAlchemyError`,
   `NoReferencedTableError`, `ValidationError`,
   `RelationshipModelNotImported`, `ResponseValidationError`) — not
   confirmed permanently closed, only quiet for 5+ days; worth a light
   recheck if any resurface.
10. **Deployment success — 0/52 all-time** — not a "failure class" in
    the taxonomy sense, but the dashboard's own `Deployment (no data)` /
    `deployment_success 0/52` line is a real, unaddressed gap this whole
    series has left untouched (canaries mostly run `--no-deploy`) —
    worth a dedicated look before any "beta" claim.

## 8. Beta readiness assessment

**Not yet beta-ready**, though meaningfully closer than at Exp048.
45.9% generation success (post-repair) and 29.7% first-try means more
than half of generations still need at least one repair cycle, and
over 70% never succeed without any repair at all — acceptable for an
actively-developed research pipeline, not yet for a product surface
users would trust unsupervised. Deployment success sits at 0/52 in the
sampled history (almost entirely because canaries default to
`--no-deploy` for cost reasons, not necessarily a real 0% deploy rate —
this is a measurement gap, not a confirmed failure, and should be
clarified before being read as a blocker). The single highest-leverage
remaining gap (JourneyCRUDFailure) is well-characterized enough to
target directly, with a proven-necessary case for a deterministic fix
(the repair loop's own 0% success rate against it rules out "just let
the LLM retry more" as a viable alternative).

## 9. Recommendation for Exp091

**One more deterministic-repair cycle is justified before a ForgeBench
milestone run** — targeting JourneyCRUDFailure's Create-path
(ownership-FK-not-injected) first, since it's the larger-volume,
better-characterized of its two sub-shapes and shares its root cause
with the smaller `NotNullViolationError`/`TimestampNotNullError` tail
(fixing it addresses 3 taxonomy entries at once). Root-cause the
Edit-path 405 shape as a follow-up investigation (its exact mechanism
isn't yet pinned down the way the Create-path one already is from prior
live observations).

**After that lands**: this is a reasonable point for a ForgeBench
milestone checkpoint (per `CLAUDE.md`'s own framing — "reserved for
milestone checkpoints, not every cycle") to get a broader, more official
reliability read across difficulty tiers than the 3-app canary provides,
now that the dominant recent bugs (MissingEndpoint, auth field
mismatches, ORM serialization) are closed and the next-highest lever is
already queued. Not recommended to jump to ForgeBench *instead of* the
JourneyCRUDFailure fix — that would spend a heavier benchmark run
re-discovering a bug this cycle already found and precisely
characterized, at higher cost than fixing it first.

**Deliverables**: this doc, `experiments.md` entry, refreshed Observatory
reading (§1–2 above; no dashboard code changes — same reasoning as every
prior investigation-only cycle, no new activation data exists yet to
justify a permanent counter). No code changes, no Cerebras calls.
**Cost: $0.**
