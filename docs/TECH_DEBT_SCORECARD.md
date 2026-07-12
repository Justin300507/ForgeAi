# Technical Debt Scorecard (Experiment 059, Part 9)

2026-07-12. Each score is A/B/C/D, backed by specific evidence from
`docs/ENGINEERING_REVIEW.md`, `docs/VALIDATOR_REVIEW.md`,
`docs/PERFORMANCE_FINDINGS.md`. This is a snapshot, not a trend claim
beyond what the cited evidence directly supports.

## Reliability — B

Real, working improvement trajectory this cycle: Exp053-055 added
failure isolation to both major backend/frontend patcher dispatch
sequences; Exp054/057 found and fixed two confirmed regressions with
exact root causes; Exp056/058 built and used a live-validation
methodology that actually caught what offline testing missed. That last
point is also the ceiling on this grade: **Exp053 itself introduced a
regression (the `patch_model_field_mismatches` NameError) that its own
40-test regression suite did not catch** — it took a live canary run
(Exp056) to surface it, months... no, days later. A well-tested
codebase still produced an undetected regression, meaning the test
suite's confidence doesn't fully match reality yet. Additionally: 2
HIGH-severity uncaught-exception paths remain open (`deployed_fixer.py:210`,
`deployment_fix_service.py:270`, Part 2/7), and the validator
result-shape inconsistency already caused one confirmed wrong-file-fix
bug (Part 3). Not a D or C — the fixes that DO ship are rigorously
verified (git-stash replay, exact-source-extraction tests); not an A —
because real, unfixed reliability gaps remain, one of which already
manifested in production.

## Maintainability — C

`app/services/v6_orchestrator.py::generate_project_v6` (911 lines,
cyclomatic complexity 135, nesting depth 7) is the highest-churn
(41 commits) function among the repo's 5 most complex — and it's the
exact function that already produced one confirmed regression from an
edit that passed its own test suite (Part 1). `app/services/deterministic_patcher.py`
is 6621 lines with 90 commits, the highest churn of any file in the
repo. Repair metadata is scattered as print-string literals with no
central registry (Part 2), and a rule-table duplication
(`_COLUMN_TYPE_RULES`/`_SCHEMA_FIELD_TYPE_RULES`) has already drifted
apart in practice (Part 2). Not a D — this session's own work (Exp053)
demonstrably reduced duplication where it was tackled (brace-matching,
`_find_free_port` not yet done). C reflects that new debt keeps forming
in the highest-risk files about as fast as it's cleared elsewhere.

## Testability — B

48 test files; Part 6 confirmed every code-changing experiment from
Exp048 through Exp057 has a corresponding, real test file — genuine,
verified discipline, not assumed. The `git stash` + exact-source-extraction
verification pattern used repeatedly this session (Exp054, 055, 057) is
a notably rigorous practice. Ceiling on the grade: Part 6 could not
verify (marked "Unknown") whether edge cases are systematically covered,
whether any tests are flaky, or whether coverage overlaps redundantly —
these weren't fabricated as findings, but the absence of an answer is
itself a testability gap. No regression test exists yet for the ~20
redundant-filesystem-walk performance issue (Part 5) or the 4-shape
validator inconsistency (Part 3) — both real, known issues with zero
test coverage protecting against a bad fix later.

## Performance — C

Three confirmed, measured findings, none catastrophic today but all real
and unaddressed: ~20 redundant full-project `os.walk`/`rglob` calls per
`validate_project()` invocation (Part 5, Finding 1) on the hottest path
in the whole pipeline (runs once per fix-loop retry attempt); 21 more
independent `rglob()` calls across `deterministic_patcher.py`
(Finding 2); a confirmed duplicate `compute_prevention_rate` computation
on every Observatory page load (Finding 6) whose result is silently
discarded. None of these currently cause a user-visible slowdown at
today's corpus size — that's exactly why this is a C and not a D: real
debt, not yet an emergency, but on a growth trajectory (see Scalability)
that will make it one.

## Observability — B

A genuine, working, actively-used asset: Observatory (Exp050) is not
theoretical — this very session used it directly to verify Exp056 and
Exp058's results. The two biggest repair dispatch mechanisms
(`run_deterministic_patches`, `run_frontend_patches`) now have automatic
per-call duration/success/exception capture (Exp053/055). Cost/token
tracking is granular and was cited by exact number in every experiment
this cycle. Ceiling on the grade: the 13 standalone validators have
**zero** logging or timing instrumentation (Part 3, confirmed via grep
returning 0 hits) — a stuck validation gives no signal about which check
is running or slow. Error propagation flattens exception detail to a
generic string in at least one live API route (`main.py:484-510`, Part
7). A silent `except Exception: pass` around `config_json` parsing
(`job_queue.py:274-277`, Part 7) could hide real data corruption with no
trace at all.

## Scalability — C

Every telemetry read (`/observatory`) re-parses `generation_log.jsonl`,
`canary_history.json`, AND the entire `experiments.md` (4074 lines,
confirmed via `wc -l`) from disk, on every single request, with zero
caching layer anywhere in the read path (Part 4/5, Findings 5-6). This
is cheap today (82 log entries, 29 canary runs) purely because the
corpus is still small — the cost scales linearly with the project's own
growing history, which is guaranteed to keep growing given this
project's own experiment cadence. The ~20-redundant-`os.walk` finding
(Performance, above) will also get proportionally worse as generated
projects grow larger. No architectural blocker to fixing this — it's
unaddressed, not unaddressable.

## Architecture — B

Real, documented, actively-being-reduced debt: 4 independent repair
dispatch mechanisms coexist by design (documented in
`docs/REPAIR_ARCHITECTURE.md`, not accidental or hidden), with a
`RepairRegistry` already designed (Exp053) as the migration target for a
future cycle with canary budget. 4 incompatible validator result shapes
coexist with no shared protocol (Part 3) — a real inconsistency, but one
whose exact blast radius (one confirmed bug) is now precisely known
rather than a vague risk. The single highest-risk function in the repo
(`generate_project_v6`) has already produced one confirmed regression.
B, not C or D, because this session's own track record shows the team
finding these issues BEFORE they compound further (Exp051's audit →
Exp053's partial consolidation → Exp056 catching what Exp053 missed →
Exp057 fixing it) — an architecture actively being hardened with
evidence, not one accumulating debt unnoticed.

## Documentation — C

19 docs in `docs/`, most exceptionally thorough and evidence-based by
this session's own standard (root cause + fix + evidence + before/after
is the house style, e.g. `docs/EXP057.md`, `docs/EXP058_VALIDATION.md`).
Ceiling on the grade: Part 8 found a **direct contradiction** between
two docs where one recommends an action the other already investigated
and found unsafe (`docs/REPAIR_DEBT.md` Rank 2 vs.
`docs/REPAIR_ARCHITECTURE.md` §4) — a future reader following the stale
doc literally would attempt a merge already proven to break a real edge
case. `REPAIR_DEBT.md` presents multiple findings as open that were
since fixed, with no errata. 8 repair-adjacent modules have zero
documentation coverage anywhere. No single doc or diagram covers the
full idea→deploy pipeline. C reflects genuine per-document quality
undermined by an accumulating lack of cross-document maintenance.

---

## Overall picture

No score below C, no score above B. The consistent theme across all 8
categories: **this project has strong, evidence-driven remediation
habits when it looks at something, but has not yet looked at everything
— and the things it hasn't looked at recently (validator infrastructure,
Observatory's own read path, cross-doc consistency) show real,
measurable debt of the same kind already fixed elsewhere.** The
highest-leverage move suggested by this pattern is not a new kind of
review, but applying the SAME rigor (audit → fix → verify → document)
that already worked for the repair subsystem to validators next — see
`docs/EXPERIMENT_BACKLOG.md` item #2.
