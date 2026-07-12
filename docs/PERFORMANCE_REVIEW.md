# Performance Review (Experiment 065, Part 5)

2026-07-12. Offline, read-only. Builds directly on Exp059's
`docs/PERFORMANCE_FINDINGS.md` (same cycle, same codebase, most findings
independently reverified rather than blindly reused) plus two new
angles this experiment specifically asked for: startup time and memory
usage.

## Reverified from Exp059 — still accurate, one has gotten worse

- **`validate_project()`'s ~20 redundant full-project `os.walk`/`rglob`
  calls** (Exp059 Finding 1) — not re-walked this cycle in full detail,
  but the underlying validator files are unchanged in this respect by
  Exp060's migration (which added a `diagnostics` parameter, not new
  filesystem scans) — the finding stands.
- **`deterministic_patcher.py`'s 21 separate `rglob()` calls** (Exp059
  Finding 2) — unchanged.
- **`compute_prevention_rate` called twice per `/observatory` request**
  (Exp059 Finding 6) — **reverified directly this cycle**: `grep -c
  "compute_prevention_rate("` shows 2 occurrences in
  `app/memory/reliability_metrics.py` (the definition + one internal
  call inside `compute_observatory`) plus 1 more direct call in
  `main.py` — confirms the duplicate-computation finding is still live,
  unfixed.
- **`experiments.md` fully re-parsed on every `/observatory` request**
  (Exp059 Finding 5) — **reverified and confirmed WORSE**: the file was
  4074 lines at Exp059's measurement; it is **4476 lines** now (+402,
  from this cycle's own 6 additional experiment write-ups, Exp059-064).
  The cost of this unaddressed finding scales directly with this
  project's own experiment cadence — every cycle that doesn't fix it
  makes the next `/observatory` request marginally slower.

## New this cycle: startup time

**No explicit `lifespan`/`@app.on_event("startup")` hook exists** in
`main.py` (confirmed via grep — zero matches for `on_event` or
`lifespan`). All initialization happens at **module import time** —
`main.py` (1477 lines) has only 11 direct top-level `app.*` imports, but
each transitively pulls in large portions of the ~56K-line backend at
process start: `app.services.project_service` alone eventually imports
the 6621-line `deterministic_patcher.py`, and `app.knowledge.lucide_icon_exports`
(3713 lines, a `frozenset` literal of ~3700 icon names) loads unconditionally
regardless of whether any request that cycle will ever need icon
validation.

**Severity: low.** None of this is asymptotically expensive (parsing a
frozenset literal of short strings, importing already-compiled `.pyc`
modules on a warm start) — this is a one-time, sub-second cost at
process boot, not a per-request cost. Flagged because "startup time" was
explicitly asked for and the answer is genuinely "no lazy-loading
anywhere, everything loads eagerly at import" — worth knowing before a
future cycle considers a serverless/cold-start deployment model, where
this cost would matter far more than it does for a long-running server
process.

## New this cycle: memory usage

**No memory profiling was performed** (would require actually running
the process, out of scope for "offline only, no live APIs"). Static
inspection found one candidate for attention: `app.core.context`'s
`GenerationContext` accumulates a growing `timeline: list[TimelineEvent]`
and `token_usage` across a single generation's full run — per Part 4's
own finding, `verification/engine.py` alone mutates a `GenerationContext`
instance at 15 distinct sites. For a single generation this is bounded
and small; **`Unknown`** whether the worker-queue path
(`app/queue/worker.py`) retains completed `GenerationContext` objects in
memory across multiple jobs in the same process (a real memory-growth
risk if so) or discards them per-job — not traced this cycle, flagged
as a genuine open question for a future cycle with profiling access.

## Prioritized fixes (unchanged ranking from Exp059, reconfirmed still valid)

1. Shared file-list for `validator_service.py`'s ~20 walks — highest
   impact, medium-high effort (12+ files).
2. Same pattern in `deterministic_patcher.py` — same shape, similarly
   high effort.
3. Drop the duplicate `compute_prevention_rate` call — low effort,
   zero behavior risk (confirmed unused-result, per Exp059).
4. Cache or tail-read `experiments.md` parsing — now measurably more
   valuable than at Exp059's measurement, given the file's continued
   growth.
5. Cache `failure_memory._load()` within one process lifetime.
6. Dedupe the two regex pairs in `database_patcher.py` — trivial,
   negligible impact.

No fix was implemented this cycle — none met the "completely
behavior-preserving, trivial to verify" bar for a drive-by change during
a review, consistent with Exp059's own standard.
