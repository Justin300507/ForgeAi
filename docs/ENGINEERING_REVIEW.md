# ForgeAI Engineering Review (Experiment 059)

2026-07-12. Offline-first Principal Engineer review, ~zero API spend.
Scope: `backend/app/` (243 measured `.py` files, 1163 functions),
`backend/scripts/`, `backend/tests/`, `docs/`, `frontend/src/pages/Observatory.jsx`.
Every finding below cites a real file:line the reviewer (or a
fork acting under identical read-only instructions) actually opened. No
generation runs, no canaries, no prompt changes were performed to
produce this review. See `docs/VALIDATOR_REVIEW.md` and
`docs/PERFORMANCE_FINDINGS.md` for the full standalone write-ups of
Parts 3 and 5; this document summarizes those plus Parts 1, 2, 4, 6, 7, 8.

---

## Part 1 — Whole Codebase Survey

Measured via a one-off AST script (written, run, and discarded — no
files left on disk) walking all 243 `.py` files under `app/` and
`scripts/`. `radon` is not installed; used a McCabe-proxy (1 + count of
If/For/While/Except/BoolOp-branches/comprehension-`if`s per function)
and a manual nesting-depth walk.

### Largest files (top 10 of 15 measured)
```
6621  app/services/deterministic_patcher.py
3713  app/knowledge/lucide_icon_exports.py   (generated data, not logic)
1692  app/verification/engine.py
1314  app/services/database_patcher.py
1250  app/services/v6_orchestrator.py
1185  app/services/validator_service.py
1173  app/repair/orchestrator.py
1131  app/services/project_service.py
 976  app/runtime/user_journey_runner.py
 790  app/repair/preflight.py
```

### Longest / most complex functions (excluding prompt-builder functions, ~90% string literal)
```
lines complexity depth  function
911   135        7      app/services/v6_orchestrator.py:generate_project_v6:118-1028
770   111        6      app/services/project_service.py:generate_project:314-1083
633   166        15     app/runtime/user_journey_runner.py:run_user_journey:344-976
603   131        7      app/services/runtime_fix_service.py:generate_runtime_fix:89-691
466   83         4      app/runtime/error_parser.py:parse_runtime_error:34-499
461   142        7      app/services/parallel_backend_service.py:generate_backend_parallel:303-763
359   83         7      app/services/database_patcher.py:patch_add_missing_model_columns:525-883
```

### Deepest nesting (top 5)
```
depth=16  app/services/undefined_symbol_validator.py:validate_undefined_symbols:24-174
depth=15  app/runtime/user_journey_runner.py:run_user_journey:344-976
depth=15  app/runtime/user_journey_runner.py:do_create:598-787
depth=11  app/services/validator_service.py:validate_imported_symbols:114-285
depth=10  app/services/schema_model_validator.py:validate_schema_model_consistency:72-268
```

### Duplicated utilities (confirmed by reading both bodies, not name-matching)
- **`_find_free_port` — genuine, byte-for-byte-identical duplicate**:
  `app/runtime/playwright_runner.py:29` (start=5173, range 50) vs.
  `app/runtime/docker_validator.py:45` (start=18000, range 100). Same
  socket-bind-probe loop, same exception handling. Trivially
  consolidatable into `app/utils/net.py::find_free_port(start, count)`.
- Brace-matching duplication was **already consolidated in Exp053**
  (`app/utils/brace_matching.py`) — not a live finding, noted to avoid a
  false positive.
- No other exact-duplicate function bodies found among
  `_extract_*`/`_split_*`/`_find_*`/`_parse_*`/`_strip_*`/`_clean_*`
  across `app/services/`, `app/repair/`, `app/utils/`,
  `app/verification/`, `app/runtime/` — remaining same-prefix names are
  semantically distinct (different domains).

### Duplicated regex (sampled, not exhaustive — 185 total `re.compile()` sites repo-wide)
3 independently-maintained "is this a password-like field" heuristics
with **different literal sets**: `database_patcher.py:688`
(`password|token`), `database_patcher.py:927`
(`password_hash|hashed_password|pwd_hash|pass_hash`),
`deterministic_patcher.py:5804` (`\bhashed_password`). Not byte-identical
duplicates, but three independently-maintained answers to the same
question — a real drift risk (a future field-name variant fixed in one
won't propagate to the other two). Broader regex-duplication census:
**Unknown** — not exhaustively diffed all 185 sites.

### Risk ranking (complexity × churn — `git log --oneline -- <file> | wc -l`)
```
90 commits  app/services/deterministic_patcher.py   — highest risk: largest file, extreme churn
41 commits  app/services/v6_orchestrator.py          — 911-line/complexity-135 function; the exact
                                                          function that already produced one real
                                                          regression (Exp053/057) from an edit that
                                                          passed its own test suite
28 commits  app/verification/engine.py
23 commits  app/services/database_patcher.py
21 commits  app/services/project_service.py
21 commits  app/runtime/user_journey_runner.py        — WORST raw complexity/depth (166/15) in the repo
20 commits  app/core/pipeline.py
17 commits  app/services/runtime_fix_service.py
16 commits  app/services/validator_service.py
```
**`generate_project_v6` is the single highest-risk function in the repo**
by this composite: highest churn among the top-5 complexity functions,
and the exact function that already caused one confirmed regression that
its own test suite didn't catch (Exp053 → Exp056 found it → Exp057 fixed
it). `run_user_journey` has the worst raw complexity/depth score in the
codebase (166/15) with slightly lower churn — a strong maintainability
risk for a future decomposition experiment, not touched this cycle.

---

## Part 2 — Repair Pipeline Review

**Pure vs. impure.** Top-level `patch_X(project_path)` functions are
inherently impure by design (file I/O is their job) — appropriate, not a
defect. Smaller inference helpers ARE already pure and correctly
separated: `_infer_column_spec` (`database_patcher.py:517-522`),
`_infer_schema_field_spec` (`database_patcher.py:1167-1171`),
`_split_params`/`_param_has_default` (`deterministic_patcher.py`),
`find_matching_brace` (`app/utils/brace_matching.py`). No mutable-default-arg
bugs found.

**Table-driven already, and a confirmed live duplication-drift bug.**
`database_patcher.py:498` (`_COLUMN_TYPE_RULES`) and
`database_patcher.py:1145` (`_SCHEMA_FIELD_TYPE_RULES`) are two parallel
rule tables — same regex patterns, the same ~15-word boolean field-name
list, `_id$`, count/qty/amount suffixes — only the output type differs
(SQL column spec vs. Pydantic annotation). **This drift is already
live**: the schema table's suffix group (line 1153) includes `"value"`;
the column table's equivalent (line 513) does not — a real behavioral
divergence from one table being edited without the other. A single
`_FIELD_TYPE_RULES = [(pattern, sql_type, sql_default, extra_import,
pydantic_annotation)]` table would serve both `_infer_column_spec` and
`_infer_schema_field_spec`.

**Failure-isolation gaps beyond what Exp053/055 already fixed** — two
new findings:
1. `deployment_fix_service.py:270` —
   `_DETERMINISTIC_FIXES[error_type](project_path, parsed_error)` has
   **no try/except**, unlike the LLM-fix path 13 lines below it
   (line 283-297, which does). A raising deterministic fix propagates
   uncaught out of `generate_deployment_fix()`.
2. `deployed_fixer.py:210-263` — `fix_deployed_app`'s `try:` wraps the
   whole dispatch loop but has **no `except`, only `finally:`**. Traced
   the caller (`main.py:484-510`): an outer try/except prevents a 500,
   but the failure discards every remaining error's fix AND the
   independent `_resync_frontend` step meant to run after
   (`main.py:499-506`), even though that step is unrelated to whatever
   raised.

**Metadata**: repair names/descriptions are scattered as print-string
literals across ~50 functions — no central registry. `reliability_metrics.py`'s
`DETERMINISTIC_PREVENTION_CATEGORIES` dict (~line 50) is a partial,
independently-maintained mapping — a new patcher won't automatically
appear there (another drift risk, same shape as the rule-table drift
above).

**Metrics**: automatic only where `_run_patch_isolated`/`FrontendPatchResult`
wrap a call (Exp053/055's work). Elsewhere (`deployment_fix_service.py`,
`deployed_fixer.py`, `preflight.py`'s individual fixes): ad hoc.

No trivial implementations attempted — documented only.

---

## Part 3 — Validator Review

See `docs/VALIDATOR_REVIEW.md` for the full write-up. Headline finding:
**4 incompatible validator result shapes** (`Diagnostic` dataclass,
plain-string `errors` lists, `JourneyStep` dataclass, raw dicts) with no
shared base — and this has already caused a confirmed live bug
(`verification/engine.py:1641-1662`'s own docstring documents an LLM
fixing the wrong file because `Diagnostic.file_path` was unset for
string-based errors). None of the 13 standalone validators have any
logging or timing instrumentation, despite `verification/engine.py`
having both extensively.

---

## Part 4 — Observatory Review

Reviewed `main.py:840-889` (the `/observatory` route),
`app/memory/reliability_metrics.py` (`compute_observatory`,
`compute_reliability_timeline`, `compute_experiment_attribution`,
`confidence_from_evidence`), and `frontend/src/pages/Observatory.jsx`
(304 lines).

**Missing metrics.** The reliability timeline (`compute_reliability_timeline`,
`reliability_metrics.py:329-351`) averages ALL apps in a canary run
together into one `avg_score` point. This directly loses the exact kind
of signal this session's own Exp056/058 work depended on — e.g. `todo`'s
99.71 → 74.4 regression would be invisible in an averaged timeline if
`blog_cms`/`crm` moved in the opposite direction the same round. No
per-app breakdown exists in the timeline data structure at all. No cost
or wall-clock-duration trend exists despite `total_cost_usd`/
`total_llm_time_s` being present in every `generation_log.jsonl` entry
(confirmed present, per this session's own Exp056 cost-log inspection).

**Missing trends / drill-down.** No click-through from a stat card or
timeline point to the underlying run's raw detail — a user seeing "score
dropped" has no in-UI way to see why (must go to raw JSON files, exactly
what this review's own author did manually for Exp056-058). No table
view alongside the trend chart (`TrendChart`, `Observatory.jsx:42-171`)
— this project's own `dataviz` skill guidance requires a table-view
alternative for any chart with ≥2 series; confirmed absent here by
reading the full component.

**Poor UX.** No manual refresh button and no polling/auto-refresh
(`Observatory.jsx:175-179`: `useEffect(..., [])` — fetches exactly once
on mount). If a canary run finishes while the page is open, the user
must reload the whole page. Loading skeleton and error states DO exist
(`Observatory.jsx:196-204`) — a genuine positive, not a gap.

**Duplicate computation, confirmed (not hypothetical).**
`main.py:882` calls `compute_observatory(gen_entries, canary_runs)`,
which internally calls `compute_prevention_rate` again
(`reliability_metrics.py:275`) and embeds the result as
`cockpit.prevention_by_category`/`cockpit.prevention_total`.
`main.py:885` separately calls `compute_prevention_rate(gen_entries)`
again for the top-level `prevention` key. Confirmed via
`Observatory.jsx:271-272`: the frontend reads `data.prevention.*`, the
SECOND call's result — the first call's embedded prevention data inside
`cockpit` is computed and discarded, unused. See
`docs/PERFORMANCE_FINDINGS.md` Finding 6.

**Expensive calculation, growing over time.** `parse_recent_experiments`
(`app/memory/experiment_log.py:17-31`) reads and regex-scans the
**entire** `experiments.md` (4074 lines as of this experiment, confirmed
via `wc -l`) on every single `/observatory` request, just to return the
last 8 entries. See `docs/PERFORMANCE_FINDINGS.md` Finding 5.

**Future scalability.** `generation_log.jsonl` (82 lines today) and
`canary_history.json` (29 runs today) are both fully re-read and
re-parsed from disk on every `/observatory` request
(`main.py:861-879`), with no caching layer. Cheap today; will not stay
cheap as both files grow — this is the same class of issue as the
`experiments.md` finding, generalized.

No redesign performed or proposed — findings only, per the task's rule.

---

## Part 5 — Performance Review

See `docs/PERFORMANCE_FINDINGS.md` for the full write-up. Headline
finding: **`validate_project()` triggers ~20 independent full-project
`os.walk`/`rglob` calls** across its 11+ delegated validator functions,
none sharing a pre-computed file list — an O(V×N) cost where O(N+V) is
achievable. Not implemented (real refactor, 12+ files, out of scope for
"behavior-preserving only"). Second finding: the same pattern at smaller
scale in `deterministic_patcher.py` (21 separate `rglob()` calls). No
fix in this category was implemented — nothing met the "trivially,
unambiguously safe" bar for a drive-by change during a review.

---

## Part 6 — Testing Review

37 files in `tests/reliability/`, 6 in `tests/adr002/`. Largest:
`test_preflight_fixes.py` (70 tests), `test_inline_chain_repairs.py`
(58), `test_frontend_rewrite_repairs.py` (44), `test_schema_cleanup_repairs.py`
(40).

**Duplicate tests**: none found among the 5 largest, most-likely-to-overlap
files — each targets a disjoint function set.
`test_json_cleaner_repairs.py` and `test_brace_matching_consolidation.py`
look similar by name but test different layers (wrapper vs. the shared
primitive it delegates to, per Exp053's consolidation) — complementary.

**Positive finding — recent test discipline is strong.** Every
code-changing experiment from Exp048 through Exp057 has a corresponding
test file (Exp048→`test_regen_arch_cache_bypass.py`,
Exp049→`test_broken_template_literal_classname.py`,
Exp050→`test_observatory.py`/`test_observatory_render.py`,
Exp053→4 test files, Exp055→`test_frontend_patch_isolation.py`,
Exp057→`test_runtime_fix_loop_scope.py`). `Unknown` for the pre-Exp048
range — not exhaustively checked.

**Unused fixtures**: spot-checked the largest file
(`test_database_patcher_and_relationships.py`) — its one helper
(`_mk_project`, line 42) is used throughout, not dead. `Unknown` beyond
this spot-check.

**Missing edge cases, overlapping coverage, flaky/slow tests**:
`Unknown` — not completed within the review's time budget for this
part; flagged as incomplete rather than fabricated.

**Prioritized next test**: given performance Findings 1/2 are the
biggest unfixed risk, the highest-value next TEST (not fix) would be a
regression test that instruments and asserts the CURRENT redundant-scan
call count as a documented baseline — so a future consolidation refactor
has a concrete "before" number to prove it didn't silently drop
coverage. Second: a small script that cross-checks `experiments.md`
against `tests/` so the "every experiment has a test" property this
review found is self-verifying going forward, not manually re-checked.

---

## Part 7 — Error Handling Review

**Broad-catch counts, top files**: `deterministic_patcher.py` (91),
`verification/engine.py` (27), `project_service.py` (23),
`runtime/user_journey_runner.py` (23), `database_patcher.py` (22),
`runtime_fix_service.py` (19), `repair/orchestrator.py` (19),
`v6_orchestrator.py` (17).

**Ranked by severity:**

1. **[HIGH — cascading job failure]** `deployed_fixer.py:210`
   try/finally-with-no-except (Part 2). A single early fix-function bug
   takes down the whole "Check & Fix" job, silently discarding unrelated
   later work.
2. **[HIGH — uncaught in a dict-dispatch path]**
   `deployment_fix_service.py:270` unwrapped deterministic-fix call
   (Part 2).
3. **[MEDIUM — possible silent data loss]** `app/queue/job_queue.py:274-277`
   (`_row_to_job`): `config = json.loads(row["config_json"] or "{}")`
   wrapped in bare `except Exception: pass` — a corrupted `config_json`
   value silently becomes `{}` with no log line, no flag on the `Job`
   object. A job could silently run with an empty/wrong config and
   nothing would ever surface the corruption.
4. **[LOW-MEDIUM]** The majority of inspected except-pass instances
   (`ai_provider.py:57-58,141-142,152-153` — cost-tracking/cache side
   effects; `runtime/backend_runner.py:39-40` — best-effort stray-process
   cleanup; `job_queue.py:159-163` — rollback-after-already-failed-transaction)
   are **legitimately defensive**, not bugs — worth stating explicitly:
   not every broad except in this codebase is a risk. A blanket
   "add logging to every except" pass would be low-value noise; the ones
   worth fixing are the ones that silently discard data or abort
   unrelated work.

**Error propagation to the user**: `_run_check_and_fix`
(`main.py:484-510`) flattens any exception to
`{"status": "error", "result": {"error": str(e)}}` — loses exception
type and structured context, making a `NameError`-class internal bug
indistinguishable from a genuine deployment failure in the API response.
Not unique to this route — most job-store patterns in `main.py` follow
the same flatten-to-string convention.

---

## Part 8 — Documentation Review

Reviewed all 19 files under `docs/`, `CLAUDE.md`, and `experiments.md`'s
recent entries.

**Outdated docs, confirmed:**
- `docs/REPAIR_DEBT.md` (Exp051) presents findings as current/unaddressed
  that were since fixed, with no errata: Rank 6 ("brace-matching
  reimplemented 3 times", lines 402-435) was fixed in Exp053. Rank 4/7
  ("4 dispatch mechanisms, one with no failure isolation", lines
  129-158) was fixed for mechanism 1 in Exp053 and again for
  `run_frontend_patches` in Exp055. A reader trusting `REPAIR_DEBT.md`
  alone today would think these are still open.
- `backend/scripts/run_canary.py:218` — the `--provider` argparse help
  text still reads `"auto already tries Gemini first with retries,
  falling back to Groq"`. Factually wrong since this session's own
  Cerebras-first reorder of `app/services/ai_provider.py` — auto now
  tries Cerebras first. Code-level staleness (a `--help` string), not
  just a doc file. Not fixed here — Part 8's own instructions require
  recommendations only, no doc/code edits.
- `CLAUDE.md`'s provider-chain description (lines 57-61) is **accurate**
  and current (verified directly against `ai_provider.py`) — noted to
  avoid a false positive.

**Contradiction, confirmed:** `docs/REPAIR_DEBT.md` Rank 2 (lines
352-391) recommends consolidating `file_writer_service.py`'s param-order
fixer into `deterministic_patcher.py`'s. `docs/REPAIR_ARCHITECTURE.md`
§4 (Exp053) investigated exactly this and found a **real semantic
difference** (bracket-tracking for `Dict[str, int]`-shaped defaults) and
explicitly did NOT merge them, recommending instead a standalone bug fix
— which Exp054 then did. `REPAIR_DEBT.md` was never updated to reflect
that its own recommendation was investigated and rejected. A future
reader following it literally would attempt a merge already shown
unsafe.

**Missing architecture docs:** no single doc describes the full
idea→deploy pipeline. Reconstructable only by reading `CLAUDE.md`'s "V15
Generation Pipeline" section (no diagram) together with
`docs/REPAIR_GRAPH.md` §7 and `docs/REPAIR_ARCHITECTURE.md` §3 — both
mermaid diagrams start mid-pipeline at the repair layer, covering
neither generation nor deploy.

**Missing diagrams:** only 2 of 19 docs contain a mermaid diagram
(`REPAIR_ARCHITECTURE.md`, `REPAIR_GRAPH.md`). `docs/FORGEAI_VNEXT_REPORT.md`
(367 lines) and `docs/V16_DEPLOYMENT_RELIABILITY_AUDIT.md` (317 lines) —
both describing multi-stage flows — have none.

**Missing onboarding docs:** no "how the pieces fit together" doc; no
maintained index/glossary of the experiment-numbering scheme (a reader
must infer "Exp048-057 = reliability hardening cycle" from
`experiments.md`'s prose).

**Missing repair docs, confirmed via cross-reference grep:** 8
repair-adjacent modules have zero mentions across all of `docs/*.md`:
`architecture_fix_service.py`, `diff_repair_service.py`,
`fixer_service.py`, `fixture_loader_service.py`, `fix_log_service.py`,
`fix_writer_service.py`, `frontend_fix_service.py`,
`runtime_fix_writer.py`. `REPAIR_DEBT.md`'s own audited-files list
confirms these were out of scope for every repair audit so far, not
just omitted from citation.

**Top 5 ranked by confusion/risk:**
1. `REPAIR_DEBT.md` Rank 2 vs. `REPAIR_ARCHITECTURE.md` §4 contradiction
   — highest risk, could cause a real regression if followed literally.
2. `REPAIR_DEBT.md`'s unflagged-stale findings — wastes a future audit's
   time rediscovering already-fixed issues.
3. `run_canary.py:218` stale help text — low risk, trivially fixable,
   actively misleads about live provider behavior.
4. No full-pipeline diagram — onboarding friction, not correctness risk.
5. 8 undocumented repair modules — moderate risk if any contains a real
   bug (unaudited = unknown coverage; `Unknown` whether any currently do).

---

## Summary of what changed vs. what was found

Nothing in this codebase was modified as part of this review beyond the
5 documentation deliverables themselves (this file, `TECH_DEBT_SCORECARD.md`,
`EXPERIMENT_BACKLOG.md`, `PERFORMANCE_FINDINGS.md`, `VALIDATOR_REVIEW.md`)
and the `experiments.md` entry. Per the task's own instruction, results
were NOT committed automatically.
