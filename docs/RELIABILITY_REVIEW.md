# Reliability Review (Experiment 065, Part 3)

2026-07-12. Offline, read-only. Covers retry loops, timeout handling,
cancellation, partial writes, resource leaks, temp file cleanup,
subprocess cleanup, thread safety, and async correctness. Does not
re-report Exp059's already-catalogued silent-failure findings
(`deployed_fixer.py:210`, `deployment_fix_service.py:270`,
`job_queue.py:274-277` — see `docs/ENGINEERING_REVIEW.md`).

## 1. Retry loops — clean

`app/retry/manager.py::RetryManager` (the main fix-escalation loop):
well-bounded, `max_attempts=5` default, `exhausted` property,
`next_strategy()` returns `None` when exhausted. `app/deployments/railway_provider.py:71`
(`_gql_retry`, 3 retries) and `:231` (`_poll_domain`, 18 attempts × 10s
= 3 min max, each subprocess call independently timed out at 15s).
**Grepped the entire `app/` tree for `while True:` — zero matches.** No
unbounded retry loop exists anywhere in the codebase.

## 2. Timeout handling — one confirmed inaccuracy, low severity

`app/deployments/railway_provider.py:224,235` — explicit timeouts (300s,
15s). `app/runtime/backend_runner.py`'s health-check loop (lines
228-245): bounded by `max_wait=15` iterations, but **the comment claims
"15s" while the actual worst case is higher** — each iteration can make
2 HTTP requests at `timeout=2` each plus a fixed `time.sleep(1)`, so a
run where every check times out could take up to `15 × (2×2+1) ≈ 75s`,
not 15s. Low severity — only matters when the backend is already
unhealthy, which the comment itself says is an acceptable cost to pay —
but the comment is measurably wrong and should be corrected for anyone
tuning this later.

## 3. Cancellation — confirmed absent

Grepped `app/queue/job_queue.py` and `app/runtime/*.py` for `cancel`,
`CancelledError`, any "cancel" job-status transition. **Found none.** No
mechanism exists for a caller to cancel an in-flight generation/verification
job once started. A long-running subprocess (uvicorn boot, npm build,
playwright session) runs to its own completion or timeout with no
external cancel hook. This is a real gap for a commercial product (a
user closing a browser tab mid-generation can't stop the backend work),
though a "not built yet" gap, not a regression.

## 4. Partial writes — repo-wide pattern, mitigated by re-validation

`app/services/fix_writer_service.py::write_fix` (and essentially every
other writer in the codebase — `file_writer_service.py`,
`database_patcher.py`) writes directly to the final path
(`open(full_path, "w")`), no temp-file-then-rename. A process kill
mid-write could leave a truncated file. Not flagged as urgent: every
write is immediately followed by a syntax/consistency check on the next
read (Exp054/064's own write-time guards), so a truncated write's most
likely failure mode is "caught as a syntax error on the next pass and
re-fixed," not "silently corrupts state" — the pipeline's constant
re-validation is an effective (if incidental) mitigation.

## 5. Resource leaks — HIGH severity, confirmed

`app/verification/engine.py::VerificationEngine.run()`: the backend
subprocess (`ctx._backend_runner`, started with `keep_alive=True` at
line 678) is only stopped at the very end of `run()`'s body (lines
1554-1559) — **not inside a `try`/`finally`**. Any exception raised in
stages 4-11 (the parallel HTTP/schema/browser/perf/a11y block at line
1467, the LLM Judge stage, or the failure-graph build at line 1550)
skips this cleanup entirely, **leaking the running uvicorn subprocess**.
Partial self-healing exists — the *next* `run()` call on the same `ctx`
stops any `prior_runner` first (lines 1345-1350) — but if the exception
propagates out of `run()` entirely, or `ctx` is discarded, the
subprocess is orphaned holding its port for the rest of the parent
process's lifetime. **This is the single highest-severity finding in
this Part.**

## 6. Temporary file cleanup — MEDIUM severity, confirmed cross-platform bug

`app/deployments/cloudflare_provider.py`: the **primary** cleanup is
solid — `deploy()`'s `finally:` block (lines 285-288) unconditionally
`shutil.rmtree`s the build temp dir on every exit path, verified against
the same object created by `_prepare_build_dir()` (line 164). The
**secondary, defense-in-depth cleanup** meant to catch directories left
behind by a hard process kill (line 151: `glob.glob("/tmp/forge-cf-*")`)
is **hardcoded to a POSIX absolute path**. This project runs on Windows
(confirmed: this session's own environment), where `tempfile.mkdtemp()`
creates directories under `%LOCALAPPDATA%\Temp\`, never literally
`/tmp/` — so this fallback glob silently matches zero files on Windows,
every time, with no error or log. The primary path is unaffected; the
one safety net meant to catch the crash-before-`finally` case is inert
on the platform this codebase actually runs on.

## 7. Subprocess cleanup — well-engineered, one confirmed dead-code finding

`app/runtime/backend_runner.py::BackendRunner.stop()` (lines 121-140)
and the non-`keep_alive` exit path (344-358): `terminate()` →
`wait(timeout=8)` → `kill()` fallback → `wait(timeout=5)`, output-drain
threads joined with their own timeout. Well-engineered.

**Confirmed dead code**: lines 421-431 of this same file
(`stdout, stderr = process.communicate()` followed by a `return
RuntimeResult(...)`) are **unreachable** — the function already returns
at line 405-419, several statements earlier, with no branch that skips
that return. Verifiable, not a style nit — this code will never execute.

## 8. Thread safety — one narrow, self-limiting risk

No `threading.Lock` in `app/verification/engine.py`, `app/providers/ai_provider.py`,
or `app/runtime/backend_runner.py`. The `ThreadPoolExecutor(max_workers=5)`
at `engine.py:1467` runs 5 concurrent verification checks, none of which
call into `ai_provider.py`'s LLM dispatch — no cross-thread hazard
there. **A real, narrow risk exists elsewhere**: `app/services/parallel_backend_service.py:185`'s
`ThreadPoolExecutor` (concurrent backend-file generation) has worker
threads that DO call `ai_provider.py`'s `generate_content()`, which
reads/writes the module-level `_provider_cooldown_until` dict with no
lock. CPython's GIL makes individual dict operations atomic (no
structural corruption), but the check-then-act pattern
(`_on_cooldown()` then later `_note_provider_result()`) has a narrow
race window where 2+ threads could each independently discover the same
dead provider and both burn a redundant failed call before either
writes the cooldown entry. Self-limiting inefficiency, not a
correctness bug.

## 9. Async correctness — not applicable, confirmed

The runtime/verification pipeline (`backend_runner.py`, `engine.py`) is
entirely synchronous, using `subprocess`/`ThreadPoolExecutor` for
concurrency, not `asyncio`. No sync-blocking-inside-async pattern exists
in this subsystem because there's no `async` code in it to mix with.
(`main.py`'s FastAPI route layer does use `async def` handlers per
framework convention — out of scope for "runtime/verification code" as
directed this cycle.)

---

## Ranked findings, this Part

1. **[HIGH] Backend subprocess leak on exception** — `engine.py`'s
   `run()` cleanup isn't in a `finally`; any exception in stages 4-11
   orphans a running uvicorn process holding its port.
2. **[MEDIUM] Cross-platform-broken defense-in-depth temp cleanup** —
   `cloudflare_provider.py:151`'s `/tmp/forge-cf-*` glob never matches
   on Windows; the primary `finally`-guarded cleanup is unaffected.
3. **[LOW-MEDIUM] Confirmed dead code** — `backend_runner.py:421-431`,
   unreachable after an earlier `return`.
4. **[LOW] Health-check loop comment undersells its own worst case** —
   claims 15s, actual worst case ~75s.
5. **[LOW] Narrow, self-limiting race window** in the unlocked
   provider-cooldown dict under `parallel_backend_service.py`'s thread
   pool — no data corruption, just a possible redundant failed call.
6. **[Gap, not a bug] No cancellation support anywhere** — a real
   product gap for a commercial release, not a regression.

**Positive findings, stated for completeness**: no unbounded retry loop
anywhere in the codebase; `RetryManager` and the Railway polling loops
are both cleanly bounded; the primary subprocess-termination and
temp-file-cleanup mechanisms are well-engineered (terminate→kill
fallback with timeouts, drain-thread joins, `finally`-guarded rmtree).
