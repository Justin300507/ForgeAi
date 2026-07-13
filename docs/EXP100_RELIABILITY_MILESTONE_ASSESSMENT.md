# Experiment 100 — Reliability Milestone Assessment

2026-07-13. Investigation only, $0, zero Cerebras calls, zero code
changes. Uses `failure_memory/generation_log.jsonl` (106 records,
2026-06-28 → 2026-07-13), `failure_memory/patterns.json` (467 lifetime
runs), `benchmark_results/canary_history.json` (46 canary runs),
`failure_memory/bundles/*.json`, the current reliability test suite,
and git commit history for exact fix-timing cross-reference — the same
methodology as every taxonomy-refresh cycle in this series (Exp083,
Exp090, Exp097).

## 1. Updated failure taxonomy (Task 1)

All-time tag frequency (`generation_log.jsonl`, 106 records):

| Tag | Count | Status |
|---|---|---|
| (untagged HTTP/workflow note, e.g. "POST /seed returned 500") | 51 | see §2 |
| `JourneyCRUDFailure` | 23 | **CLOSED** (both sub-shapes) |
| `AttributeError` | 13 | **CLOSED** |
| `SQLAlchemyError` | 4 | 3 historical, 1 open (new, Exp099) |
| `ConfigAttributeError` | 3 | **CLOSED** |
| `ImportError` | 3 | historical, not recurring |
| `ResponseValidationError` | 2 | historical, not recurring |
| `PydanticSerializationError` | 2 | **CLOSED** |
| `Unknown` | 1 | historical |

Last-30-record window (2026-07-11T20:51 → 2026-07-13T04:38, the
Observatory's own rolling window): 10 `AttributeError` (all
timestamp-verified pre-dating their respective fix commits — see §2),
3 `JourneyCRUDFailure` (all pre-dating their fix commits), 1
`PydanticSerializationError` (pre-dates its fix by 13 minutes), 1
`SQLAlchemyError` (today, from Exp099's r1, not yet root-caused).
**Zero new occurrences of any closed class since its respective fix
commit** — confirmed by direct timestamp-vs-commit-time
cross-reference for every entry in the window, not by assumption.

## 2. Closed reliability classes (Task 2)

Removed from the active taxonomy, each with proof of closure (fix
commit + live-validation commit + confirmation that no telemetry entry
post-dates the fix):

| Class | Root cause | Fix commit(s) | Live-validated |
|---|---|---|---|
| `JourneyCRUDFailure` — Create-path (74% of instances) | POST handler never assigns ownership FK before `db.add()` | `7aee5ad` (Exp092) | Exp093, 2 clean canaries |
| `JourneyCRUDFailure` — Edit-path (405) | Test harness hardcoded PUT; architect legally allowed to choose PATCH | `ede6aea` (Exp095) | Exp096, 2 clean canaries |
| `AttributeError` — `SignupRequest.username` | Auth-field cross-file naming drift | `0d2c74d` (Exp083-086) | live-confirmed gone |
| `AttributeError` — seed-route field guessing (`User.name`, `UserCreate.password`) | Wave-4 seed generation guesses fields with no Wave-2/3 visibility | `acd1f46` (Exp098) | Exp099, confirmed absent in live telemetry |
| `ConfigAttributeError` | Config/Settings missing attrs, case-sensitivity | `f2721c5`, `85514e5` | no recurrence since |
| `PydanticSerializationError` | ORM instance returned where Pydantic schema expected | `11db3d1` et al. (Exp087-089) | 2 confirmatory canaries |
| Strategy-memory stale blacklist | `regenerate_module` reused stale evidence across generations | Exp079-082 | live-confirmed |
| Regen-cache staleness (Exp048) | `REGENERATE_ARCH` strategy's cache hit reintroduced pre-patch content | Exp048-058 arc | live-confirmed |

Also closed earlier in the broader arc (pre-Exp077, confirmed via
memory and unchanged since): router-export mismatches, dict-unpack
constructor kwargs (the *read-path* shape), ORM dict-response
conversion, relationship/back_populates strip, ownership-FK
*read-path* attribute drift (`_patch_ownership_fk_attribute_drift`,
distinct from the Create-path write-path fix above).

## 3. Remaining issues, ranked (Task 3)

| # | Issue | Frequency | Severity | Self-heal | Deterministic? |
|---|---|---|---|---|---|
| 1 | Seed-route reliability (constructor-kwarg dict-unpack `TypeError`, e.g. `Users(**udata)` with a guessed field; cascading Create-entity 400s from unseeded FK lookups, e.g. inventory_manager's "Category does not exist") | Moderate — ~4-6 confirmed instances across 3+ projects (todo, inventory_manager, gym_tracker-shaped scar tissue) | Medium — mandatory `/seed` endpoint silently broken in shipped apps; occasionally cascades into a scored CRUD failure | Low — `_patch_attr_access_mismatches` explicitly doesn't cover `**kwargs` unpacking (different AST shape); LLM repair loop rarely resolves it | Yes, root cause well understood (Exp097's Wave-3/4 gap), but not yet implemented for this specific AST shape |
| 2 | Assignment-target rewriting risk in `_patch_attr_access_mismatches` (identity-field cluster: `username`/`display_name`/`full_name`) | Low — 1 confirmed live instance (Exp099) | Potentially high if it recurs (silent data corruption, not a crash) but unconfirmed as widespread | N/A — silent, never triggers the repair loop at all since no exception is raised | Yes, but narrow — needs corpus evidence of prevalence before a fix is justified |
| 3 | New `SQLAlchemyError`/`StatementError` (SQLite data-type `TypeError`) | Very low — 1 confirmed instance (Exp099, today) | Unknown — not yet root-caused | Partial — repair loop attempted, ran out of budget | Unknown, needs investigation |

None of these three clears this project's own established "measure
before build" bar ([[feedback_measure_before_build]]: no validator
without corpus prevalence + severity + ROI first) — each has either
low confirmed frequency, unconfirmed severity, or is a single fresh
data point. This is a materially different situation than every prior
taxonomy-refresh cycle in this series (Exp083, Exp090, Exp097), each of
which found a single clear, high-frequency, high-severity, deterministic
class with a well-understood fix path (JourneyCRUDFailure at 74%
Create-path share; AttributeError's 2 confirmed active incidents both
directly root-caused). No such class remains today.

## 4. Cumulative improvement since Exp048 (Task 4)

Partitioned `generation_log.jsonl` at Exp048's fix-commit time
(2026-07-11 ~07:00 UTC):

- **Post-repair success rate** (eventual `succeeded=True`, any number of
  fix attempts): **31.8% → 47.5%** (66 records before, 40 after; +15.7
  points, +49% relative).
- **First-try (zero-fix) success rate** — the Observatory's own "North
  Star" metric: **25.8% → 30.0%** (+4.2 points). Confirmed against the
  Observatory's own computed `first_try_success_rate` for the current
  30-record window (30.0%, exact match).
- **Average Forge Score**: **75.6 → 84.0** (+8.4 points).
- **Qualitative**: every dominant, high-frequency failure class present
  at Exp048's time (JourneyCRUDFailure's two sub-shapes, the seed-field
  AttributeError class, PydanticSerializationError, auth-field/Config
  naming drift) is now closed and live-validated — zero recurrences in
  telemetry since each fix.

The gap between the two success-rate figures (+15.7 vs +4.2) is itself
informative: most of this period's gains came from making the
**deterministic repair layer** dramatically more effective at catching
what generation still gets wrong on the first pass, not from improving
first-pass generation quality itself — consistent with this project's
strategy of investing in post-generation correction rather than prompt
engineering for this class of bug.

## 5. Does any remaining class justify another repair experiment? (Task 5)

**No.** Per §3's ranking, every remaining candidate fails at least one
of this project's own required bars (frequency, severity, or
confirmed-not-speculative root cause) at a level that would justify
immediate implementation. The most promising candidate (seed-route
reliability) is real and deterministic, but its current evidence base
(a handful of instances, mostly non-score-impacting since `/seed`
itself isn't a scored journey step) doesn't yet clear the bar this
series has consistently required before writing new deterministic
infrastructure — unlike JourneyCRUDFailure's Create-path (74% of a
large, clearly-defined bucket) or the seed-field AttributeError class
(2 confirmed, directly-traced active incidents), which did.

## 6. Recommendation: begin ForgeBench (Task 6)

**Yes.** Exp090 (the prior milestone assessment) explicitly set the bar
for this: *"Only after [Create-path ownership AND Edit-path 405 fixes
land] is this a good point for a ForgeBench milestone checkpoint...
running it before fixing an already-characterized, high-impact bug
would just re-discover it at higher cost."* Both of those have since
been root-caused, fixed, and live-validated (Exp091-096), plus an
additional full reliability thread beyond what Exp090 anticipated
(AttributeError seed-fields, Exp097-099). The condition Exp090 itself
set for "not yet" no longer holds. No real, broad ForgeBench-scale run
exists in this project's history to compare against (only test-harness
simulation fixtures, `test_forgebench_sim*`) — this would be the first
genuine milestone-scale checkpoint, exactly the use this runner was
reserved for per `CLAUDE.md`.

## 7. Beta Readiness Scorecard (Task 7)

| Dimension | Assessment |
|---|---|
| **Architecture** | Stable. V15 pipeline (plan → architect → parallel backend/frontend → deterministic patch → verify → score → repair → deploy) unchanged in shape since V15's introduction; no architectural rewrites needed this cycle. Wave-based parallel generation (models/schemas/routes) is the one identified *structural* source of cross-file drift (§3 #1, and the root cause behind 3 of this series' last 6 experiments) — a known, accepted trade-off (speed vs. consistency), not a defect. |
| **Repair pipeline** | Strong and improving. Deterministic pre-repair patching now covers ownership-FK assignment (both read and write paths), attribute-access mismatches (SQLAlchemy + Pydantic), auth-field naming, config attributes, ORM serialization, router/schema drift. Confirmed 0%-LLM-self-heal for several bug classes now converted to $0 deterministic corrections. The regression-detection-and-revert safety net (pre-Exp077) continues to catch mid-run degradations independently of any specific patcher. |
| **Runtime** | Healthy on the dominant paths. Endpoint smoke tests consistently show 100% pass rates across recent canaries. CRUD journey healthy in the large majority of final states; one confirmed exception this cycle (Exp099 r1's journey degraded from 10/11 to 6/11 over its repair loop, traced to an unrelated new `SQLAlchemyError` — itself evidence the repair loop can still *introduce* regressions the scoring formula doesn't always catch, a live, real risk worth future attention but not yet frequent enough to scope a fix). |
| **Security** | Moderate, not yet a focus area. Recent security reviews (e.g. Exp096's live runs) consistently score in the 70s/100 (medium risk) — common findings: missing per-route auth on some endpoints, permissive CORS, missing rate limiting, missing max_length constraints. No critical findings in recent runs. Not addressed by any experiment in the Exp077-099 arc (out of scope for the reliability thread) — a candidate area for future, separate investment, not urgent for a reliability-focused beta. |
| **Benchmark correctness** | Substantially improved this cycle specifically. Exp094-096 found and fixed a genuine test-harness bug (hardcoded PUT) that was producing false JourneyCRUDFailure reports against spec-compliant generated code — the benchmark itself, not just the generated apps, needed correction. `ExchangeRecorder`'s forensic capture verified intact post-fix (Exp096, Exp099). No other test-harness-side defects identified this cycle. |
| **Deterministic reliability** | High and validated. Reliability test suite: 51/54 passing (same 3 pre-existing, unrelated, environment-specific failures — stale fixture directory, missing `jose` package, 2 unrelated write-corruption subtests — carried across every recent experiment, none touching the areas under active development). Every new patcher this cycle (Exp092, Exp095, Exp098) shipped with its own dedicated regression test file and was verified via full-corpus replay against real, previously-generated projects before being trusted, catching 3 real would-be bugs before they shipped (Exp092's `ast.Index` bug, Exp098's reverted mechanical-reciprocal design, Exp088's schema-selection bugs). |
| **Remaining risks** | (1) Seed-route reliability (constructor-kwarg field mismatches, cascading unseeded-FK Create failures) — real, moderate-frequency, not yet fixed, doesn't yet meet this project's own ROI bar. (2) Assignment-target rewriting in the identity-field synonym cluster — a confirmed, narrow, latent data-corruption risk, needs more corpus evidence. (3) A freshly-surfaced, not-yet-root-caused `SQLAlchemyError`/`StatementError` (Exp099) — single data point, needs more telemetry. (4) Security posture is untouched by this reliability arc and sits at "medium risk" by the pipeline's own review scoring — worth a dedicated look eventually, not blocking. None of these are the kind of dominant, high-frequency, well-understood class this series has closed one after another for the last ~15 experiments. |

## 8. Recommendation

**ForgeBench**, not Exp101. Run the broader, heavier multi-app suite
(`run_forgebench.py`) as the milestone checkpoint this reliability arc
has been building toward since Exp090 explicitly deferred it pending
exactly the fixes that have now landed. This will also generate fresh,
broader corpus evidence on the three open, low-confirmed-frequency
risks in §7 — informing whether any of them graduates to a dedicated
Exp101+ cycle, rather than guessing now with a thin evidence base.

**Deliverables**: this doc, `experiments.md` entry.
**Cost: $0, zero Cerebras calls, zero code changes.**
