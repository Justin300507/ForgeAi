# ForgeAI Repair Intelligence (Experiment 069, Part 6)

2026-07-12. Direct code investigation of `app/repair/`, plus
`app/services/deterministic_patcher.py` and `app/services/database_patcher.py`
(both physically located under `services/`, not `repair/`, despite
being deterministic repair logic — a naming/organization inconsistency
worth noting for `docs/TECH_DEBT_MASTER.md`). Builds on, does not
duplicate, `docs/WRITE_PIPELINE.md`'s (Experiments 066-067) exhaustive
coverage of the actual write mechanism.

## Strategy dispatch (the part not yet documented elsewhere)

`app/repair/orchestrator.py::run_attempt()` (line 1027+) dispatches
`FixStrategy` values with a real asymmetry: **`PATCH_FILE` and
`REGENERATE_FILE` both route through `_apply_fix_group()` (line 590)
via a fall-through `else` branch (lines 1091-1092)** — they are not
separately implemented despite being distinct enum values.
`REGENERATE_MODULE` routes to `_regenerate_module()` (line 779, the
write path Experiment 067 hardened). `REGENERATE_ARCH` routes to
`_regenerate_architecture()` (line 873) and `break`s immediately — a
full regen covers every diagnostic group in one shot, no per-group
loop. **`SWITCH_MODEL` is not a separate code branch at all** — it
only changes `cfg.provider`/`cfg.model_hint`, which then flows through
whichever of the strategies above is actually selected. Which strategy
it pairs with by default was not confirmed this cycle (Unknown).

## Input

`run_attempt(ctx: GenerationContext, cfg: StrategyConfig)`. Diagnostics
come from `ctx.all_diagnostics()`; if a `failure_graph` exists with
suppressed downstream symptoms, only root-cause diagnostics are kept
(lines 1053-1060) — a real, confirmed root-cause-only filtering
mechanism, not aspirational. Diagnostics are grouped via
`group_diagnostics(all_diags, max_groups=6)` before any strategy runs.

## Output / write mechanism

All three strategies ultimately produce `(modified_paths, fix_content)`
and funnel into the write layer Experiments 066-067 hardened —
`write_fix()`/`atomic_write_text()`/`resolve_safe_path()` for
`_apply_fix_group()`'s 4 call sites and, as of Experiment 067,
`_regenerate_module()`'s one call site. `_regenerate_architecture()`
recurses into `generate_project_v6()` → `write_files()`. Full detail
in `docs/WRITE_PIPELINE.md` — not re-derived here.

## Call graph / execution order

`run_attempt()`, lines 1082-1150: group diagnostics → dispatch strategy
per group → **deterministic + preflight patches run AFTER the LLM
strategy, on top of it** (lines 1097-1107: `run_deterministic_patches()`,
`patch_database_py()`, `preflight.run()`, all wrapped in a bare
`except Exception: print warning`) → re-verify (`VerificationEngine.run()`)
→ re-score (`ScoringEngine.score()`) → regression check → commit or
revert.

**Confirmed risk**: the bare `except Exception` around the
deterministic-patch block means a patcher crash is silently swallowed
to a print statement, not surfaced as a diagnostic or a failed attempt.
No test found asserting this doesn't mask a real regression.

## Failure isolation / rollback

Confirmed, whole-project, extension-filtered snapshot —
`_ProjectSnapshot` (lines 923-953) walks the entire project tree
(skipping `node_modules`/`dist`/`build`/`__pycache__`/`.git`/`venv`/`.venv`)
and captures every file matching
`{.py, .jsx, .js, .ts, .tsx, .json, .css, .html, .toml, .yaml, .yml, .txt, .env, .md}`
as raw bytes **before every single fix attempt**, not just the files
about to be touched. Its own comment (lines 929-932) documents this
was itself a prior bug fix: the OLD snapshot only covered `.py`/`.jsx`,
so a "reverted" attempt used to silently keep partial changes to
`package.json`/CSS/configs.

Revert is triggered by the regression logic at lines 1126-1147:
**the score is the primary arbiter, a diagnostic-count regression is
only a tiebreaker** — its own comment (lines 1118-1125) documents this
itself was a fix: treating newly-*visible* diagnostics (the server
started for the first time, so checks previously skipped now run) as
regressions used to revert a genuine 42.5→63 score improvement every
time.

## Metrics

`backend/failure_memory/strategy_outcomes.json` is the only per-strategy
success/failure ledger found — a coarse **7-bucket** taxonomy
(`AttributeError`, `ConfigAttributeError`, `ImportError`, `SyntaxError`,
plus three non-pattern buckets `api`/`browser`/`contract`), each with
`{strategy: {successes, tries}}`. **This is a genuinely different,
coarser granularity than the 21-pattern `patterns.json` taxonomy
Experiment 068 built — the two are not reconciled anywhere in the
codebase**, a real cross-cutting data-model gap (see
`docs/TECH_DEBT_MASTER.md`). Notable numbers: the `contract` bucket
has by far the most volume (`patch_file`: 39/113 successes,
`regenerate_arch`: 8/34); `AttributeError` via `regenerate_module` is
0/3; `ImportError` via `regenerate_module` and `switch_model` are both
1/1 (too small a sample to generalize from).

## Known bugs found while reading (direct code inspection, not from experiments.md)

1. The bare `except Exception` around the deterministic-patch block
   (line 1106) swallows patcher crashes to a print — no test coverage
   confirming this never masks a real regression.
2. `_regenerate_architecture`'s `break` after the first group (line
   1088) means if `group_diagnostics` ever orders groups such that
   REGENERATE_ARCH isn't actually the best-suited strategy for the
   FIRST group, the strategy still fires for the entire diagnostic
   set. Not confirmed as an active bug — a structural sharp edge.

## Test coverage

`backend/tests/reliability/` has `test_repair_failure_isolation.py`,
`test_repair_registry_design.py` (a `RepairRegistry` was *designed*
but never deployed — per this project's own prior-experiment history,
corroborated by the file existing without a corresponding production
`RepairRegistry` class found this cycle), `test_regen_arch_cache_bypass.py`
(cache-bypass behavior only, not general REGENERATE_ARCH correctness),
`test_repair_stage1_consolidation.py`, `test_inline_chain_repairs.py`,
`test_json_cleaner_repairs.py`, `test_schema_cleanup_repairs.py`,
`test_sql_constructor_and_auth_repairs.py`, `test_deployment_repairs.py`,
`test_frontend_rewrite_repairs.py`. **No test file by name targets
`PATCH_FILE` or `SWITCH_MODEL` strategy correctness specifically** —
only narrower behavioral slices (cache bypass, failure isolation) are
covered for the strategy-dispatch layer itself; most existing repair
tests target individual deterministic patchers, not `run_attempt()`.

## Deterministic patcher inventory

Counted directly, not estimated: **90 total deterministic repair
functions** across three files:
- `app/services/deterministic_patcher.py` — 66 `_patch_*`/`patch_*` functions
- `app/services/database_patcher.py` — 8 functions
- `app/repair/preflight.py` — 16, via a clean `@preflight.register(name, priority)`
  decorator registry (`preflight.py:42-47`) — fixes run in explicit
  priority order, e.g. `fix_pyjwt`=10, `fix_bcrypt`=11,
  `fix_config_missing_settings_instance`=13 *before*
  `fix_config_missing_attrs`=14, with an explicit ordering-dependency
  comment at line 156, up through `fix_database_py`=50.

Existing categorization: `app/memory/reliability_metrics.py::DETERMINISTIC_PREVENTION_CATEGORIES`
(lines 17-75) maps most `deterministic_patcher.py`/`preflight.py` keys
into 7 human buckets (Import validation, Symbol validation, Schema
validator, Entity validator, Syntax validator, Pydantic patcher,
Auth/routing patcher, Frontend patcher) plus an explicit "Other"
catch-all for unmapped keys — this categorization already exists and
should not be rebuilt.

## Future improvements (informed by this cycle's own findings, not speculative)

1. Reconcile `strategy_outcomes.json`'s 7-bucket taxonomy with
   `patterns.json`'s 21-pattern taxonomy (also independently flagged
   by Experiment 068's own roadmap, recommendation #18).
2. Add dedicated tests for `PATCH_FILE`/`SWITCH_MODEL` strategy
   dispatch correctness, not just individual patcher behavior.
3. Surface (not swallow) deterministic-patch crashes inside
   `run_attempt()`'s bare `except Exception` block.
4. Decide whether to deploy the already-designed `RepairRegistry` or
   remove its test file — a half-finished subsystem is worse than
   either committing to it or cleanly retiring it.
