# ForgeAI Performance Review V2 (Experiment 069, Part 9)

2026-07-12. Extends `docs/PERFORMANCE_REVIEW.md` (Exp065 — found ~20
redundant `os.walk()` calls in `validate_project()`, did a complexity/
startup/memory delta pass). This document covers only categories that
review did not.

## Finding #1 (the highest-value finding in this document) — Redundant AST parsing across the validator layer

`ast.parse()` is called independently across **23 distinct files,
~51 total call sites**, including all 12 wired validator modules
(`database_validator.py`, `duplicate_class_validator.py`,
`endpoint_validator.py`, `global_statement_validator.py`,
`orm_validator.py`, `router_export_validator.py`,
`schema_model_validator.py`, `self_shadow_validator.py`,
`session_validator.py`, `stub_handler_validator.py`,
`undefined_symbol_validator.py`, plus the 2 dead-code validators —
see `docs/VALIDATOR_INTELLIGENCE.md`), all orchestrated from
`validator_service.py::validate_project()`. Each independently walks
the project directory and re-parses the same `.py` files with its own
`ast.parse()` call.

**Quantified**: for a project with N Python files run through the
~12 active validators in one verification pass, this is up to N×12
redundant AST parses of identical file content — no shared/cached AST
anywhere in this pipeline. This is the direct sibling finding to
Exp065's ~20 redundant `os.walk()` calls, and arguably the higher-value
fix of the two, since AST parsing is more CPU-expensive than a
directory walk.

**The clearest caching opportunity found this cycle**: share one AST
parse across all validators in a single verification pass, keyed by
file path + mtime (or a content hash). This is a pure performance win
with no behavior-preserving risk if implemented as a read-through
cache in front of the existing per-validator `ast.parse()` call sites
— every validator's own logic stays identical, only the parse itself
is shared.

## Finding #2 — Subprocess/shell: confirmed clean (cross-reference)

Already covered in `docs/SECURITY_REVIEW_V2.md` Finding "Confirmed
clean" section — all subprocess call sites use list-form arguments,
no `shell=True`. Included here only as a performance cross-reference:
list-form subprocess calls also avoid shell-parsing overhead, a minor
secondary benefit of the same finding.

## Finding #3 (narrow, heuristic) — Regex catastrophic-backtracking sweep

A targeted grep for nested-quantifier shapes (`(x+)+`, `(x*)+` style)
across `app/services/*.py` and `app/repair/*.py` found **zero
matches**. This is a narrow heuristic sweep, not a full per-pattern
ReDoS review of every regex in the codebase — reported with that
caveat, not as a clean bill of health.

## Not exhaustively checked this cycle (flagged, not assumed clean)

- **Memory**: the 5,901-file `llm_cache/` directory's access pattern
  specifically was not verified — Unknown whether it's ever loaded as
  a whole versus accessed per-key.
- **JSON re-parsing**: a targeted check for repeated
  `json.load(open(...))` calls against the large telemetry files
  (`patterns.json`, `arch_db.json`, `cost_log.json` — several 100-400KB)
  within `app/memory/*.py` and `app/repair/*.py` found no obvious
  same-request re-parse pattern in the files checked, but this was
  **not exhaustive** across the full codebase.
- **Large-response streaming**: whether large LLM responses or
  generated-project zips are ever fully buffered in memory where
  streaming would be more appropriate — not traced this cycle.

## Cross-reference: Exp065's still-valid findings, not re-derived

`docs/PERFORMANCE_REVIEW.md` already covers (still current, not
re-verified this cycle since no code changed in these areas):
`validate_project()`'s ~20 redundant `os.walk()` calls, startup/memory
profile, and general complexity-vs-performance delta analysis.

## Synthesis: the two redundancy findings together

Exp065's `os.walk()` finding and this cycle's `ast.parse()` finding
are the same underlying architectural pattern at two different levels
— **`validator_service.py::validate_project()`'s dispatch-to-12-independent-modules
design means every validator re-derives its own view of the project
from scratch** (its own file list via `os.walk()`, its own parsed AST
via `ast.parse()`), rather than one shared "project view" being built
once and handed to all 12. This is not 12 separate performance bugs;
it's one architectural decision (validator independence, presumably
for isolation/simplicity) with a consistent, compounding performance
cost. Worth treating as one finding in `docs/TECH_DEBT_MASTER.md`
and `docs/FORGEAI_V2.md`, not two.
