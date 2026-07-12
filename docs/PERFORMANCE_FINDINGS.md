# Performance Findings (Experiment 059, Part 5 + Observatory-specific findings)

2026-07-12. Offline, read-only review. Nothing was implemented except
where explicitly noted as trivially, unambiguously behavior-preserving
(none qualified this pass — see the note at the end).

## Finding 1 (HIGH IMPACT): ~20 redundant full-project directory walks per `validate_project()` call

`app/services/validator_service.py:996` (`validate_project`) calls at
least 11 independent validator functions sequentially, and **each one
does its own independent `os.walk`/`rglob` over the same project
directory tree** (confirmed by reading the call site, lines 1045-1109,
and cross-referencing each callee's own file scan):

- `validate_database` → `app/services/database_validator.py:71` (`os.walk`)
- `validate_architecture` → `app/services/architecture_validator.py:44` (`os.walk`)
- `validate_endpoints` → `app/services/endpoint_validator.py:35,178` (2 separate walks)
- `validate_undefined_symbols` → `app/services/undefined_symbol_validator.py:28` (`os.walk`)
- `validate_self_shadowing_functions` → `app/services/self_shadow_validator.py:17` (`os.walk`)
- `validate_orm_usage` → `app/services/orm_validator.py:27,121` (2 separate walks)
- `validate_session_management` → `app/services/session_validator.py:9` (`os.walk`)
- `validate_schema_model_consistency` → `app/services/schema_model_validator.py:80` (`os.walk`)
- Plus `duplicate_class_validator.py:16` and `global_statement_validator.py:14`
- `validator_service.py` itself has 6 more independent `os.walk` calls
  at lines 51, 119, 298, 589, 630, 782, 1150, for checks not delegated
  to sub-modules.

That's **~20 independent full-tree filesystem walks of the same project
directory within a single `validate_project()` call**, and
`validate_project()` itself runs multiple times per generation (once per
fix-loop retry attempt — see Exp056/057's own investigation of the retry
loop, `docs/EXP056_BASELINE.md` / `docs/EXP057.md`). None of these share
a pre-computed file list. This is the single largest concrete
performance finding in the codebase: O(V × N) cost (V validators, N
files) where one shared `os.walk` producing a file list once, passed to
all V validators, would make it O(N + V).

**Behavior-preserving to fix?** Not trivially — requires changing every
validator function's signature to accept a pre-computed file list
instead of `project_path`, touching 12+ files. **Not implemented** this
cycle (real refactor, not a no-op; out of scope for "implement only if
completely behavior-preserving").

## Finding 2 (MEDIUM): Same pattern in `deterministic_patcher.py`

21 separate `rglob()` calls across `app/services/deterministic_patcher.py`'s
patcher functions (lines 614, 1607, 1758, 3682, 4021, 4800, 4910, 5053,
5511, 5598, 5726, 5779, 5827, 5872, 5932, 6020, 6119, 6168, 6219, 6414,
6447 — several scanning `*.jsx` specifically), each in a separate
top-level patcher function, all invoked sequentially from
`run_deterministic_patches`/`run_frontend_patches` (whose call sequences
Exp053/055 already worked on for a different reason — failure isolation,
not this). Same class of redundancy as Finding 1, smaller scope.

## Finding 3 (LOW): Duplicated regex definitions (not a recompile-in-loop bug)

`app/services/database_patcher.py:359-360` (`class_re`, `col_re` inside
`patch_model_field_mismatches`) and `database_patcher.py:544-545,549-550`
(the same two patterns plus two more, inside a different function)
define the same two regex patterns twice in two different functions.
Each is compiled once per function call, before its `for mf in
models_dir.glob(...)` loop (verified by reading lines 330-365) — **not**
a per-iteration recompilation bug, just duplicated definitions. Low
severity; worth consolidating into a shared module-level pair only if
these functions are touched again for another reason.

## Finding 4 (LOW): Uncached JSON reload in `failure_memory.py`

`app/memory/failure_memory.py:21` (`_load()`) re-reads and re-parses
`_STORE_PATH` from disk on every call, with 3 call sites in the same
file (lines 231, 303, 337 — `record_run`, `get_top_patterns`,
`print_summary`). If more than one fires per generation (plausible —
`record_run` at the end, `get_top_patterns`/`build_prompt_injection`
earlier), that's 2+ redundant reads of the same small JSON file per
generation. Low impact today (small file), real, citable, and a real
case of missing memoization.

## Finding 5 (Observatory-specific, growing over time): `/observatory` re-parses the entire `experiments.md` on every request

`main.py:840-889` (the `/observatory` route) calls
`parse_recent_experiments(backend_root.parent / "experiments.md", limit=8)`
on every single request. `app/memory/experiment_log.py:17-31`
(`parse_recent_experiments`) reads the **entire file**
(`text = experiments_md_path.read_text(...)`) and regex-scans all of it
(`_HEADING_RE.finditer(text)`) just to return the last 8 entries.
`experiments.md` is **4074 lines** as of this experiment (confirmed via
`wc -l`) and growing by one entry per experiment cycle — this cost
scales linearly with the project's own experiment history, for a result
that only needs the tail. A future cycle could either cache the parse
result (invalidated on file mtime change) or read only the last N KB of
the file and reparse forward from there.

## Finding 6 (Observatory-specific): `compute_prevention_rate` computed twice per `/observatory` request

`main.py:882` calls `compute_observatory(gen_entries, canary_runs)`,
which **internally** calls `compute_prevention_rate(recent, window=window)`
(`app/memory/reliability_metrics.py:275`) and embeds the result as
`cockpit.prevention_by_category`/`cockpit.prevention_total`. `main.py:885`
**separately** calls `compute_prevention_rate(gen_entries)` again, whose
result becomes the top-level `data.prevention` key. Confirmed via
`frontend/src/pages/Observatory.jsx:271-272`: the frontend reads
`data.prevention.total_preventions`/`data.prevention.by_category` — the
top-level key from the SECOND call — never `data.cockpit.prevention_by_category`/
`data.cockpit.prevention_total` from the first. **The first computation's
prevention result is discarded, unused.** A real, confirmed duplicate
computation, not a hypothetical one. Small cost today (82
`generation_log.jsonl` entries), but a clean, low-risk future fix: either
have `compute_observatory` accept an already-computed prevention dict, or
drop the internal call and read it from the top-level `prevention` key
where it's actually used.

## No unnecessary subprocess calls found

Not exhaustively audited this pass — **Unknown** rather than claiming
completeness.

## Prioritized fixes (impact vs. effort)

1. **Finding 1** — shared file-list for `validator_service.py`'s ~20
   walks. Highest impact (cuts redundant I/O roughly 10-20x per
   validation pass), medium-high effort (touches 12+ function
   signatures). Best candidate for a dedicated future experiment, not a
   quick fix.
2. **Finding 2** — same pattern in `deterministic_patcher.py`. Same
   shape, similarly high effort given 21 call sites.
3. **Finding 6** — drop the duplicate `compute_prevention_rate` call.
   Low effort (one function signature change or one deleted call),
   low-but-real impact, zero behavior risk since the discarded result is
   provably unused by the frontend.
4. **Finding 5** — cache or tail-read `experiments.md` parsing. Low-medium
   effort, impact grows over time as the file grows (already 4074 lines).
5. **Finding 4** — cache `failure_memory._load()` within one process
   lifetime, invalidated on `_save()`. Low effort, low-but-real impact.
6. **Finding 3** — dedupe the two regex pairs in `database_patcher.py`.
   Trivial effort, negligible performance impact, pure cleanliness.

## Implementation note

No fix from this list was implemented — none met the bar of "completely
behavior-preserving" at low enough risk to change without a dedicated
review pass and its own regression tests (even Findings 4/6, the
smallest, touch shared caching/state and deserve their own test-backed
cycle rather than a drive-by edit during a review experiment).
