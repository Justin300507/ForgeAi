# Experiment 083 — Current Reliability Taxonomy Refresh

2026-07-12. Investigation only, $0, zero Cerebras calls. Method: reused
existing telemetry (`backend/scripts/failure_report.py`,
`backend/failure_memory/patterns.json`, `backend/failure_memory/generation_log.jsonl`,
98 entries) plus direct code reading and one real generated project on
disk (`generated_projects/todo_list_app`) — no new generation, per
constraint.

## 1. Updated failure taxonomy (all 23 recorded classes, 206 all-time instances)

| Class | Stage | All-time count | % | Last seen | Status |
|---|---|---|---|---|---|
| MissingEndpoint | generation | 48 | 23.3% | 07-11 | **CLOSED** — Exp077→082 thread |
| JourneyCRUDFailure | integration | 32 | 15.5% | 07-12 | **ACTIVE**, declining (see §2) |
| AttributeError | runtime | 22 | 10.7% | 07-12 | **ACTIVE**, dominated by 1 sub-cause |
| ImportError | runtime | 14 | 6.8% | 07-12 | ACTIVE, low severity per-instance |
| ConfigAttributeError | runtime | 13 | 6.3% | 07-07 | **CLOSED** (see §3) |
| SQLAlchemyError | runtime | 11 | 5.3% | 07-06 | STALE (6+ days, likely closed) |
| ModuleNotFoundError | runtime | 10 | 4.9% | 07-12 | ACTIVE |
| RouterExportMismatch | build | 9 | 4.4% | 07-06 | **CLOSED** (Exp021) |
| SyntaxError | build | 8 | 3.9% | 07-12 | ACTIVE, residual (dominant shape fixed by Exp049) |
| FrontendBuildError | build | 7 | 3.4% | 06-30 | **CLOSED**-ish (Exp049 fixed dominant cause, 0 recurrences since) |
| NoReferencedTableError | runtime | 6 | 2.9% | 06-27 | STALE |
| PydanticSerializationError | runtime | 5 | 2.4% | 07-11 | ACTIVE, low count |
| NotNullViolationError | runtime | 5 | 2.4% | 07-12 | ACTIVE, overlaps JourneyCRUDFailure's "Create entity" shape |
| ValidationError | runtime | 3 | 1.5% | 07-01 | STALE |
| RelationshipModelNotImported | runtime | 3 | 1.5% | 06-22 | STALE |
| TimestampNotNullError | runtime | 2 | 1.0% | 07-12 | active, negligible volume |
| ResponseValidationError | runtime | 2 | 1.0% | 07-06 | STALE |
| 6 more classes | — | 1 each | <0.5% | various | negligible |

## 2. Frequency, recency-weighted (last 30 generations, `generation_log.jsonl`)

The all-time taxonomy is cumulative since 2026-06-21 — over three weeks
of a fast-changing codebase — so it understates classes that are
*currently* dominant and overstates ones fixed long ago. Recomputed
against the last 30 generation_log entries and, for the sharpest signal,
the last 20 (the most recent ~1 day of activity):

| Class | Last-30 count | Last-20 count | Trend |
|---|---|---|---|
| **AttributeError** (SignupRequest.username specifically) | 9/30 | 6/20 | **rising** — this one sub-cause alone |
| JourneyCRUDFailure | 3/30 | 3/20 | declining vs. its 07-06 spike (17/32 all-time instances landed on one day) |
| All other classes | ≤2/30 each | ≤1/20 each | flat/low |

Overall generation success rate, last 30: 43.3% (13/30). Of the 17
failures in that window, **9 (53%) share the exact same dominant error
string**: `AttributeError: 'SignupRequest' object has no attribute
'userna[me]'`.

## 3. Deep-dive: the dominant active issue

Grepped every `generation_log.jsonl` entry whose `dominant_errors`
contains this exact string (9 of 98 total, all from 2026-07-11/12):

- **100% correlation, zero exceptions**: every one of the 9 has
  `prevention_counts._patch_auth_routes == 0` — the deterministic
  "known-good auth template" injection (`app/services/deterministic_patcher.py`,
  `_patch_auth_routes()`) **never fired** for any of these runs.
- **100% terminal**: all 9 have `succeeded: false`, `fix_count` ranging
  3–5 (i.e. the repair loop exhausted every attempt and never fixed it —
  0% same-run self-heal rate, worse than most other classes), and scores
  clustered tightly at 70.7–74.4 (capped in "needs repair," never near
  deploy-ready).
- Read `_patch_auth_routes()` directly (lines 2180–2219): it only injects
  the known-good template when `app/models/user.py` or `users.py`
  exists. Read the actual injected template
  (`_build_auth_routes_template`, lines 1941–2140): it correctly uses
  `req.email`/`req.password`/`req.display_name` throughout — it does
  **not** reference `.username` anywhere. So when injection *does* fire,
  this bug cannot occur; the failures are 100% concentrated in runs where
  the gate didn't trigger, leaving the LLM's own originally-generated
  `auth_routes.py` in place — which, across many independent generations
  for "user accounts"/"user login" apps, apparently guesses `.username`
  as a natural field name often enough to be the dominant failure.
- Checked `generated_projects/todo_list_app` (a real, later-repaired
  instance of the same idea) directly: `app/models/` has both `user.py`
  and `users.py`, and today's `auth_routes.py` carries the correct
  template (`_read_password` sentinel present, no `.username`) — so the
  gate condition *should* pass for this app shape; the exact reason it
  sometimes doesn't (timing relative to Wave 2.5's model-aliasing shim
  creation, a different model filename in some architectures, or a later
  fix-loop attempt re-generating `auth_routes.py` from scratch) is **not
  yet pinned down to one specific mechanism** — that precision is this
  cycle's main open question for Exp084, not something to guess at
  further without code changes (out of scope for an investigation-only
  cycle).
- Distinct from the existing "Auth Completeness" check
  (`repair/auth_completeness.py`) — that system verifies required auth
  *endpoints* exist (currently 100% healthy per the dashboard) and has no
  visibility into whether the request schema's *field names* match what
  the route body actually accesses. This bug is a genuine, currently
  unmonitored blind spot for that system, not a regression in it.

## 4. Ranked table — Impact = Frequency × Severity

Severity scale (1–5): 5 = total, unrecoverable crash blocking the whole
app; 4 = blocks a core user workflow (auth, CRUD) but rest of app
functions; 3 = degrades score/UX but core function survives; 2 = minor;
1 = cosmetic.

| Class | Frequency (last-30) | Severity | Impact | Deterministic? |
|---|---|---|---|---|
| **AttributeError (SignupRequest.username)** | **9** | **4** (blocks signup — the first required step of nearly every generated app) | **36** | **Deterministic** (100%-correlated gating bug, not LLM variance) |
| JourneyCRUDFailure | 3 | 4 (blocks core CRUD) | 12 | Mixed — "Create entity" sub-shape (NOT NULL ownership-FK) is deterministic and usually self-heals same-run; "Edit entity: 405" sub-shape less well characterized this cycle |
| ModuleNotFoundError | ~2 (est. from last-30 proportion) | 5 (startup crash) | ~10 | Model-dependent (varies which module) |
| SyntaxError | ~1 | 5 (build crash) | ~5 | Residual — dominant shape (Exp049) fixed; remaining instances likely model-dependent edge cases |
| ImportError | ~2 | 4 | ~8 | Model-dependent |
| NotNullViolationError | ~1 | 4 (overlaps JourneyCRUDFailure) | ~4 | Deterministic, same family as JourneyCRUDFailure's Create-entity shape |
| PydanticSerializationError | ~1 | 3 | ~3 | Model-dependent |

*(Frequencies beyond AttributeError and JourneyCRUDFailure are estimated
from their last-30 proportions rather than individually re-verified line
by line, since their counts are small enough that per-instance grep would
cost more effort than the ranking needs — they are all clearly below the
top two regardless of the exact figure.)*

## 5. Answers to Task 5

- **Highest-frequency deterministic issue**: AttributeError
  (SignupRequest.username auth-template gating gap) — 9/30, 100%
  gate-correlated, not LLM variance.
- **Highest-severity deterministic issue**: tie between the same
  AttributeError class (blocks signup — nothing else in the app is
  reachable without it) and the NotNullViolationError/JourneyCRUDFailure
  ownership-FK family (blocks core CRUD). The auth issue wins on
  frequency and total non-recovery (0% same-run self-heal vs. the FK
  family's typically-successful same-run repair), so it's the clearer
  overall pick.
- **Highest-frequency model-quality (non-deterministic) issue**:
  ModuleNotFoundError and ImportError — both vary by which specific
  module the LLM forgets or misnames each generation, not a single fixable
  code path.

## 6. Estimated reliability gain if the top deterministic issue were eliminated

In the last 30 generations: 13/30 succeeded (43.3%), 9/30 failed with
this exact error as the sole recorded dominant error and were otherwise
scoring 70.7–74.4 — meaningfully close to, not catastrophically far from,
the ~85–95 range comparable successful runs reach. If this class were
eliminated (either the gate reliably fires, or a fallback deterministic
patch corrects `.username`→`.email`/`.display_name` post-hoc when it's
detected), and assuming these 9 runs would then resolve similarly to
comparable successful ones (a reasonable but not certain assumption —
some may have secondary blockers currently masked by this dominant one):

**Projected last-30 success rate: up to (13+9)/30 = 73.3%**, vs. today's
43.3% — a potential **+30 percentage point** improvement, nearly
doubling the window's success rate. Even a partial fix (e.g. gate fires
correctly in most but not all of these cases) would still be the single
largest available lever currently visible in the taxonomy — no other
active class accounts for anywhere near 53% of a recent failure window.

## 7. Closed vs. active issue inventory

**Closed / superseded this cycle** (excluded from ranking): MissingEndpoint
(Exp077–082), ConfigAttributeError (multiple validation runs confirmed
zero recurrences), RouterExportMismatch (Exp021), FrontendBuildError
(Exp049 fixed the dominant template-literal cause, zero recurrences
since 06-30).

**Stale, likely closed but not independently re-confirmed this cycle**
(low-priority to verify further, not worth spending cycles on unless
they resurface): SQLAlchemyError, NoReferencedTableError, ValidationError,
RelationshipModelNotImported, ResponseValidationError, and the six
single-instance classes.

**Active**: JourneyCRUDFailure, AttributeError (dominated by the
SignupRequest.username sub-cause), ImportError, ModuleNotFoundError,
SyntaxError (residual), PydanticSerializationError, NotNullViolationError,
TimestampNotNullError.

## 8. Recommendation for Exp084

**Fix the `_patch_auth_routes()` injection gate.** This is the clear
single highest-impact target: highest recency-weighted frequency (9/30,
rising), high severity (blocks signup entirely), 100% deterministic
correlation (not LLM variance — literally never fires for any of the 9
failing runs), 0% same-run self-heal (the repair loop cannot fix this on
its own, unlike most other active classes), and the largest single
projected reliability gain of anything currently in the taxonomy
(+~30 points on last-30 success rate).

Exp084's first task should be root-causing the *exact* mechanism behind
the gate's failure — this cycle narrowed it to "the known-good template
never gets applied" with 100% confidence, but not yet to a single
specific reason (candidates: pipeline-stage ordering relative to Wave
2.5's model-aliasing shim creation; a model filename shape the gate's
`user.py`/`users.py` check doesn't recognize; or a later fix-loop
`_regenerate_module`/`_apply_fix_group` call re-writing `auth_routes.py`
from scratch after a correct injection already happened). Only after that
root cause is confirmed should a fix be implemented — consistent with
this project's own established investigate-then-fix discipline.

**Deliverables**: this doc, `experiments.md` entry. No code changes, no
Cerebras calls. **Cost: $0.**
