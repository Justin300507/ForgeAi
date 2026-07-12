# Dead Code Audit (Experiment 065, Part 8)

2026-07-12. Offline, read-only. All findings backed by direct grep
verification (zero-other-call-site confirmation), not assumption.

## No confirmed-dead application-logic function found this pass

Two samples checked directly:

1. **`app/repair/preflight.py`'s `_fix_*` functions** (13 sampled) —
   each showed exactly 1 grep occurrence (only their own `def` line),
   which looked like strong dead-code evidence at first. **Investigated
   directly and found to be a false positive**: these are registered
   via `@preflight.register("name", priority=N)` decorators (confirmed
   at `preflight.py:111,737`), reachable through
   `PreflightRegistry.run()`'s internal dispatch, not by direct name
   reference anywhere — so a single grep occurrence for an actively-used
   function is CORRECT, not evidence of dead code.
2. **`app/utils/*.py` helpers** (7 sampled, non-decorator-based) — all
   had 2+ real occurrences (def + genuine call site). None dead.

## Methodology finding — the most valuable output of this Part

**Naive grep-based "unused function" detection systematically
false-positives on any decorator-registered code in this repo.** The
`preflight.py` case above is the clearest example, but the same pattern
applies to any future registry-based subsystem — including, notably,
`app/repair/registry.py::RepairRegistry` (designed in Exp053, not yet
wired into live dispatch) if it's ever migrated to. **Any future
dead-code sweep of this codebase must check for `@X.register(...)`
decorators before concluding a single-occurrence function is unused.**

## Real, quantified legacy-code finding — higher confidence than the marker search

Grep for "legacy"/"deprecated"/"TODO: remove"/"obsolete" found only 2
hits, both from this cycle's own Exp060 work (`context.py:82`,
`engine.py:71`) — the codebase has essentially no self-labeled dead
code via comments.

**But `main.py` still wires up endpoints for orchestrator versions v6
through v15**, and reachability of the pre-v15 versions is genuinely
uneven:

| Version | File size | Imported by anything besides `main.py`? |
|---|---|---|
| v8 | 35 lines | **0 files** |
| v9 | 35 lines | **0 files** |
| v10 | 95 lines | **0 files** |
| v12 | 168 lines | **0 files** |
| v7 | 398 lines | 5 files |
| v11 | 291 lines | 1 file |
| v14 | 488 lines | 1 file |

**v8/v9/v10/v12 (333 combined lines) are reachable only through their
own `main.py` endpoint handlers** — confirmed via grep, matching
`CLAUDE.md`'s own claim that only `/project/v15` is "the current, live
pipeline" and older versions are "kept only as historical fallback, not
under active development." This is real, high-confidence dead-weight in
the API layer specifically — distinct from and complementary to
Exp059's finding about `deterministic_patcher.py`'s internal
duplication. Removing these 4 orchestrator versions' endpoints (and
their now-unreachable files) is a low-risk, well-evidenced cleanup
candidate.

## Not completed this cycle

- **Unused imports**: a planned 10-file random sample was not completed
  within this review's time budget — flagged as **Unknown**, not
  guessed.
- **Duplicate implementations beyond Exp059's known `_find_free_port`
  case**: none found in the comparisons actually made, but not
  exhaustively searched beyond those — flagged as **Unknown** rather
  than asserting completeness.

## Ranked recommendations

1. **Remove or archive v8/v9/v10/v12 orchestrator endpoints** — highest
   confidence, clearest evidence, lowest risk (confirmed zero real
   callers beyond their own dead-end `main.py` handlers).
2. Apply the "check for decorator registration first" rule to any
   future dead-code sweep — prevents wasted investigation time and
   false-positive removal proposals.
3. Complete the unused-imports sweep in a future cycle — genuinely not
   done this time, not a low-value gap, just an incomplete one.
