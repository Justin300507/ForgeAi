# ForgeBench v1.0 — 25-Application Reliability Benchmark

2026-07-13. Live, 25-app benchmark against the LIVE V15 pipeline
(`generate_project_v15`, Cerebras provider, `deploy=False`), per the
user's explicit spec. First genuine, full-scale system-level evaluation
in this project's history — no prior real ForgeBench run exists (only
test-harness simulation fixtures, `test_forgebench_sim*`).

**Total cost: $1.8934. Total tokens: 3,155,630. Total wall-clock time:
~3.04 hours (10,944.5s), of which 1.5 hours (5,403s) was spent on 6
timed-out generation attempts.**

## 1. Executive summary

| Metric | Value |
|---|---|
| Total apps attempted | 25 |
| Successful generations (didn't crash/hang) | 19/25 (76.0%) |
| Fully succeeded (pipeline's own `succeeded` flag) | 4/25 (16.0%) |
| First-pass success (zero repair attempts needed) | 4/25 (16.0%) — **identical to post-repair**, see §3 |
| Deploy-ready proxy (Forge Score ≥ 80, this project's `FORGE_DEPLOY_THRESHOLD`) | 7/25 (28.0%) |
| Avg Forge Score (all 25, crashed=0) | 57.0 |
| Avg Forge Score (19 completed generations only) | 75.0 |
| Avg repair iterations (19 completed) | 2.89 |
| Avg generation time (19 completed, excludes timeouts) | 291.7s (4.9 min) |
| Avg generation time (all 25, includes 900s timeouts) | 437.8s (7.3 min) |
| Avg cost/app (all 25) | $0.0757 |
| Avg cost/app (19 completed) | $0.0997 |
| Avg tokens/app (19 completed) | 166,086 |

## 2. Per-app results table

| # | App | Status | Score | Succeeded | First-pass | Fix attempts | Cost | Time | Failure class |
|---|---|---|---|---|---|---|---|---|---|
| 1 | todo_list | OK | 98.0 | **Yes** | Yes | 0 | $0.0364 | 86.3s | — |
| 2 | notes_app | OK | 97.6 | No | No | 5 | $0.0808 | 355.5s | NEW_UNCLASSIFIED (vite build) |
| 3 | blog_cms | OK | 56.4 | No | No | 4 | $0.0558 | 238.6s | JourneyCRUDFailure |
| 4 | inventory_manager | OK | 76.9 | No | No | 5 | $0.0800 | 355.0s | JourneyCRUDFailure |
| 5 | crm | OK | 86.9 | No | No | 3 | $0.0716 | 259.3s | (none tagged) |
| 6 | expense_tracker | OK | 69.6 | No | No | 3 | $0.1019 | 211.5s | NEW_UNCLASSIFIED (UserIdNotInjectedError) |
| 7 | project_management | OK | 88.4 | **Yes** | Yes | 0 | $0.0885 | 126.0s | — |
| 8 | task_manager | OK | 71.3 | No | No | 5 | $0.1155 | 570.9s | AttributeError |
| 9 | recipe_manager | OK | 70.1 | No | No | 4 | $0.1170 | 306.7s | NotNullViolationError |
| 10 | library_management | OK | 76.9 | No | No | 4 | $0.1179 | 355.0s | JourneyCRUDFailure |
| 11 | student_management | OK | 93.5 | No | No | 2 | $0.1129 | 312.7s | (none tagged) |
| 12 | employee_directory | **TIMEOUT** | 0.0 | No | No | — | $0.0000 | 900.5s | (execution-level hang) |
| 13 | help_desk | **TIMEOUT** | 0.0 | No | No | — | $0.0000 | 900.5s | (execution-level hang) |
| 14 | gym_tracker | OK | 86.4 | **Yes** | Yes | 0 | $0.0809 | 152.0s | — |
| 15 | event_management | OK | 67.9 | No | No | 4 | $0.0980 | 339.3s | JourneyCRUDFailure |
| 16 | restaurant_ordering | **TIMEOUT** | 0.0 | No | No | — | $0.0000 | 900.4s | (execution-level hang) |
| 17 | appointment_booking | **TIMEOUT** | 0.0 | No | No | — | $0.0000 | 900.4s | (execution-level hang) |
| 18 | sports_league_manager | **TIMEOUT** | 0.0 | No | No | — | $0.0000 | 900.5s | (execution-level hang) |
| 19 | volunteer_management | **TIMEOUT** | 0.0 | No | No | — | $0.0000 | 900.4s | (execution-level hang) |
| 20 | real_estate_listings | OK | 24.2 | No | No | 3 | $0.0988 | 315.7s | NEW_UNCLASSIFIED (missing route file) |
| 21 | hotel_booking | OK | 96.5 | No | No | 3 | $0.1624 | 376.4s | NEW_UNCLASSIFIED (vite build) |
| 22 | course_management | OK | 76.9 | No | No | 4 | $0.1222 | 401.6s | NEW_UNCLASSIFIED (UserIdNotInjectedError) |
| 23 | vehicle_fleet_manager | OK | 85.5 | **Yes** | Yes | 0 | $0.1094 | 245.9s | — |
| 24 | donation_tracker | OK | 76.9 | No | No | 3 | $0.1067 | 262.9s | JourneyCRUDFailure |
| 25 | medical_clinic_manager | OK | 24.9 | No | No | 3 | $0.1367 | 270.5s | NEW_UNCLASSIFIED (syntax error) |

(`20_real_estate_listings` and `11_student_management` were each deferred
to the end of the run after repeated execution-level failures, per
§4 — both eventually completed normally when retried last.)

## 3. Reliability metrics

- **First-pass success**: 4/25 = 16.0%
- **Post-repair success**: 4/25 = 16.0% — **identical to first-pass.**
  Zero apps that started with `fix_attempts > 0` ended up `succeeded=True`
  this run. Several improved their Forge Score substantially through
  repair (e.g. several apps in the 70s-90s after 3-5 fix attempts) but
  none crossed into the pipeline's own "succeeded" state — a real,
  concerning signal about repair-loop effectiveness in this broader
  sample, distinct from and worth weighing against this project's
  narrower canary-validated fixes (Exp091-099), which were each
  confirmed working on their specific target shapes.
- **Build success** (`build_ok`): 13/19 completed generations (68.4%)
- **Runtime success** (`runtime_ok`): 8/19 completed generations (42.1%)
- **CRUD success**: **not directly measurable this run** — the
  "Integration" scoring dimension was `N/A (excluded)` for every single
  app (confirmed: `crud_ok` is `None` for all 25 entries, not just the
  timeouts), a genuine data-collection gap in this benchmark's setup,
  not a generated-app property. `JourneyCRUDFailure` as a *failure
  class* (5/25, §4) is the best available substitute signal, but
  doesn't tell us the CRUD pass rate for apps that didn't hit it.
- **Deploy-ready** (Forge Score ≥ 80 proxy — `deployed` itself is always
  `False` by construction since this run used `--no-deploy`): 7/25
  (28.0%)
- **Avg Forge Score**: 57.0 (all 25) / 75.0 (19 completed)
- **Avg repair iterations**: 2.89 (19 completed)
- **Avg tokens/app**: 166,086 (19 completed) / 126,225 (all 25)
- **Avg cost/app**: $0.0997 (19 completed) / $0.0757 (all 25)

## 4. Execution-level findings (this benchmark's own reliability, not the generated apps')

This is the single most significant finding of ForgeBench v1.0, and it
is **not** a generated-application defect:

**6/25 attempts (24.0%) hung during generation** and had to be killed
by an automated 15-minute subprocess timeout added mid-run (a
new `--timeout`/subprocess-isolation mechanism built into
`forgebench_v1.py` after 2 hangs were first caught manually via
tasklist memory-delta comparison — confirmed genuine hangs, not slow-
but-progressing work, since process memory was byte-for-byte identical
across consecutive checks). All 6 affected different apps
(`employee_directory`, `help_desk`, `restaurant_ordering`,
`appointment_booking`, `sports_league_manager`, `volunteer_management`)
— not one specific idea, ruling out an idea-specific trigger. The hang
rate was 0/10 for the first 10 apps and 6/15 (40%) for the remaining
15, suggesting the risk **increases as the run progresses** rather than
being uniformly random.

Separately, the *outer* wrapper process running this benchmark script
was itself killed by the environment (not by any hang or by this
script's own logic) at least 5 times over the course of the run,
unpredictably, roughly every 10-40 minutes of wall-clock regardless of
whether work was progressing normally. Mitigated by restructuring to
one-app-per-invocation (`--limit 1`) with automatic retry, so no
completed app's data was ever lost — but this materially slowed the
benchmark (a run that should have taken under 2 hours of pure
generation time took over 3 hours end-to-end).

**Low available system memory (~3.5GB of 16GB, ~22% free) was observed
during the affected period.** Killing one identified orphaned process
did not meaningfully free memory, so this is reported as a **plausible,
not conclusively proven**, contributing factor — not a confirmed root
cause. No single blocking call or pipeline stage was confirmed as the
hang site (out of scope to investigate mid-benchmark per this
experiment's own rules).

**This is a first-class engineering finding, not a footnote**: a 24%
attempt-level hang rate (and a separate, unrelated wrapper-process
instability) would make a 100-app ForgeBench v1.1 run substantially
more expensive and slower than its generation-only numbers suggest, and
deserves investigation before scaling.

## 5. Failure taxonomy, ranked by frequency × severity

| Rank | Class | Frequency | Severity | Freq×Sev | Notes |
|---|---|---|---|---|---|
| 1 | Execution-level hang (TIMEOUT) | 6/25 (24%) | High (3) | 18 | Not a generated-app defect — see §4 |
| 2 | JourneyCRUDFailure | 5/25 (20%) | High (3) | 15 | **See §6 — recurred despite Exp091-096's fixes** |
| 3 | NEW_UNCLASSIFIED: vite:esbuild build failure | 2/25 (8%) | High (3) | 6 | Frontend build-time JSX/syntax error, 2 apps |
| 3 | NEW_UNCLASSIFIED: UserIdNotInjectedError | 2/25 (8%) | High (3) | 6 | **Same class name as the ownership-assignment thread (Exp091-093) — see §6** |
| 5 | AttributeError | 1/25 (4%) | Medium (2) | 2 | Single instance, task_manager |
| 5 | NotNullViolationError | 1/25 (4%) | Medium (2) | 2 | Single instance, recipe_manager |
| — | NEW_UNCLASSIFIED: syntax error in main.py | 1/25 (4%) | High (3) | — | One-off, excluded from ranking per this benchmark's own rule |
| — | NEW_UNCLASSIFIED: missing route file | 1/25 (4%) | High (3) | — | One-off, excluded from ranking |
| — | Untagged (`crm`, `student_management`) | 2/25 (8%) | — | — | High score (86.9, 93.5) but `succeeded=False` with no specific failure class recorded — see §3's repair-rescue-rate finding |

## 6. Top remaining engineering opportunities (multi-app, deterministic, ≥2 occurrences)

Per this benchmark's own rule, isolated one-off failures (syntax error
in `main.py`, missing route file — 1 instance each) are excluded below.

1. **Execution-level hang/timeout (6/25, 24%)** — see §4. Highest
   priority by raw impact: affects benchmark and (by extension) real
   user-facing reliability more than any single generated-app bug this
   run found. Recommend investigating before any larger-scale run.

2. **`JourneyCRUDFailure` (5/25, 20%)** — the *exact same failure
   class* that Exp091-096 spent six experiments root-causing and fixing
   (Create-path ownership-FK assignment, Edit-path 405 method
   mismatch), each individually confirmed via offline replay, full-
   corpus replay, and live canary validation. Its recurrence as the
   **#1 generated-app failure class** in this broader, fresh 25-app
   sample is the most important correction this benchmark makes to
   Exp100's conclusion that "no dominant deterministic failure class
   remains." Two honest possibilities, not adjudicated here (out of
   scope for this benchmark's own "no mid-run investigation" rule):
   either a *different* sub-shape of JourneyCRUDFailure exists beyond
   the two already fixed, or the fixed sub-shapes' coverage has gaps
   this broader sample happened to surface. Either way, this needs a
   fresh root-cause pass, not an assumption that the thread is closed.

3. **`UserIdNotInjectedError` (2/25, 8%)** — this error class name
   matches *exactly* the ownership-FK-injection failure Exp091-093
   fixed and live-validated (`_patch_missing_ownership_assignment`).
   Its recurrence in 2 fresh apps (`expense_tracker`, `course_management`)
   is a second, independent signal (alongside #2) that the ownership-
   assignment fix's coverage may have gaps — worth checking whether
   these 2 apps' specific model/FK shape falls outside what
   `_patch_missing_ownership_assignment` currently detects.

4. **Frontend build failures (`vite:esbuild Transform failed`, 2/25,
   8%)** — a genuinely new opportunity area: this entire Exp077-100
   reliability arc focused exclusively on backend correctness (auth,
   ORM, ownership, attribute access, journey-runner alignment); no
   experiment in this series has investigated frontend build-time
   JSX/syntax failures. 2 independent occurrences (`notes_app`,
   `hotel_booking`) is a modest but real signal.

## 7. Cost analysis

- Total spend: **$1.8934** across 25 attempts (19 billed generations +
  6 free timeouts).
- Cost was NOT dominated by the timeouts (they cost $0 — the LLM calls
  that did complete before the hang still got billed within the
  per-app total, but the hang itself added no extra API cost, only
  wall-clock time).
- Cost per completed app ranged from $0.0364 (todo_list, first-pass
  success, 0 repairs) to $0.1624 (hotel_booking, 3 repair attempts) —
  roughly a 4.5x spread, correlating with repair-attempt count more than
  app complexity per se.
- Extrapolating to a 100-app v1.1 at this run's observed rates (~24%
  hang, ~$0.10/completed app, ~$0 for hangs): approximately **$7.60**
  in direct API cost, but **12+ hours of wall-clock time** at this
  run's effective throughput (~7.3 min/app average including hangs) —
  the wall-clock cost, not the API cost, is the real scaling concern
  given the execution-level instability found in §4.

## 8. Recommendation

**Pause for targeted investigation before ForgeBench v1.1.** The
evidence does not support proceeding directly to a 100-app run:

1. The 24% execution-level hang rate would make a 100-app run
   unreliable and far slower than its API cost alone suggests — this
   needs investigation (or at minimum, environment hardening / a
   documented mitigation) before multiplying the attempt count 4x.
2. `JourneyCRUDFailure` re-emerging as the #1 generated-app failure
   class directly contradicts Exp100's "no dominant deterministic
   class remains" conclusion. Running a larger, more expensive
   benchmark on top of an unexamined regression/gap in an
   already-"closed" thread would be poor sequencing.
3. `UserIdNotInjectedError`'s recurrence reinforces #2 rather than
   standing alone — both point at the same underlying thread needing
   a fresh look.

Recommended next step (Exp101, investigation-first, matching this
project's own established discipline): root-cause (a) why
`JourneyCRUDFailure` and `UserIdNotInjectedError` recurred in 7 of
these 25 fresh apps despite the Exp091-093/094-096 fixes, and (b)
what's actually blocking during the execution-level hangs (does it
correlate with a specific pipeline stage across the 6 affected apps?).
Only after those are understood — fixed, or explicitly ruled out as
non-deterministic/environmental — does a 100-app v1.1 run become good
evidence rather than an expensive repeat of the same open questions.

**Deliverables**: this report, `experiments.md` entry,
`backend/scripts/forgebench_v1.py`, `backend/scripts/_forgebench_worker.py`,
`backend/benchmark_results/forgebench_v1_results.json`.
**Cost: $1.8934, 25 live generation attempts (19 completed, 6 timed out).**
