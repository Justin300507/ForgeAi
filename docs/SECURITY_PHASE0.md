# Security Phase 0 (Experiment 070)

2026-07-12. Implements every Critical/High security finding from
Experiment 069's review (`docs/SECURITY_REVIEW_V2.md`). All 5 launch
blockers named in this experiment's own mission are closed. One
finding was discovered during this cycle's own implementation work
that Experiment 069 did not catch — documented below, not silently
folded into the original list.

## Task 1 — SECRET_KEY

**File**: `backend/app/dependencies/auth.py`

**Before**: `SECRET_KEY = os.environ.get("SECRET_KEY", "please-set-SECRET_KEY-env-var-in-production")`
— a hardcoded, publicly-visible-in-source fallback. Any deployment
that forgot to set the env var would silently sign every JWT with a
key anyone could read directly from the repository.

**After**: `_validate_secret_key()` runs at module-import time (i.e.
at process startup, since `auth.py` is imported near the top of
`main.py`) and raises `RuntimeError` with a clear, actionable message
if `SECRET_KEY` is: (a) unset/empty, (b) a known placeholder/weak
value (blocklist includes the exact prior hardcoded default, both
underscore and hyphen spellings, plus common placeholders like
`changeme`/`password`/`secret`), or (c) shorter than 32 characters.
**ForgeAI never auto-generates a secret** — a silently-generated
"production" secret would be just as dangerous (every restart would
mint a new key, silently invalidating every session with no warning).

**Also fixed, found necessary while implementing this task**: `auth.py`
now calls `load_dotenv()` itself. Previously, nothing upstream of
`auth.py`'s module-level `SECRET_KEY` read (`main.py`, `app/database.py`)
called `load_dotenv()` first, meaning a `SECRET_KEY` defined only in
`.env` (not exported in the shell) was silently invisible — the
insecure default could kick in even for a developer who correctly set
`SECRET_KEY` in `.env` and never knew it wasn't being read. Verified
via a regression test that writes a real `.env` file and confirms the
key IS picked up.

**Verified**: 6 regression tests (missing key, known placeholder, the
exact old hardcoded default string, too-short key, strong key
succeeds, `.env`-file-only key is picked up) — all passing, each via a
genuinely fresh subprocess import (not a re-import trick), matching
how the real failure happens at process startup.

## Task 2 — Rate limiting

**New file**: `backend/app/middleware/rate_limit.py` — a simple,
dependency-free (no slowapi/redis, per this experiment's "keep it
simple" and $0-budget rules), in-memory, per-client-IP, per-named-bucket
fixed-window limiter, attached via FastAPI's `Depends()`.

**Documented, known limitation** (not hidden): single-process only —
does not coordinate across multiple uvicorn worker processes. Under N
worker processes, the effective limit is roughly N× the configured
value. Acceptable for ForgeAI's current single-process deployment
model (`docs/SYSTEM_DESIGN.md`); a multi-process deployment would need
a shared store instead — out of scope for this "no unrelated refactors"
cycle.

**Applied to** (`main.py`):
| Bucket | Limit | Routes |
|---|---|---|
| `auth` | 5 requests / 60s per IP | `POST /register`, `POST /login` |
| `generation` | 10 requests / 60s per IP | `POST /generate`, `/architect`, `/backend`, `/frontend`, `/project`, `/project/v6` through `/v15`, `/project/tournament`, `POST /jobs` |
| `deploy` | 10 requests / 60s per IP | `POST /deploy/github`, `/deploy/railway`, `/deploy/cloudflare` |

Repair and verification are not separately callable endpoints (they
run inside the generation pipeline — confirmed via a full route
listing of `main.py`), so rate-limiting the generation entry points
above covers them as the mission intended.

**Verified**: 4 regression tests (limit enforced after the threshold,
independent buckets don't cross-contaminate, `Retry-After` header
present on a 429, and — the wiring check, not just the mechanism —
every named route actually carries the dependency).

## Task 3 — Project path validation (found broader than Experiment 069's original finding)

Experiment 069 found one gap: `file_writer_service.py:513-523`'s
`project_name` was never validated before being joined into `base_dir`.
**Auditing every project-path construction site this cycle found 7
total unguarded sites, not 1** — 6 more live directly in `main.py`:

| Site | Function | Severity |
|---|---|---|
| `file_writer_service.py:523` | `write_files()` | High — this `base_dir` is `shutil.rmtree()`'d on every regeneration |
| `main.py` (job-retry path lookup) | `_run_job_retry` | Medium |
| `main.py` (existence check) | job-retry decision logic | Medium |
| `main.py` (check-and-fix, 2 sites) | `_run_check_and_fix` | Medium — feeds into `fix_deployed_app()` and a live Cloudflare redeploy |
| `main.py` **`delete_job`** | `DELETE /jobs/{job_id}` | **Critical — `shutil.rmtree()` directly on the unguarded path** |
| `main.py` **`delete_all_jobs`** | `DELETE /jobs` | **Critical — same, in a loop over every job** |

**The two `delete_job`/`delete_all_jobs` sites are the single most
severe finding of this entire experiment cycle** — more severe than
Experiment 069's original finding, since a `job.project_name` value
shaped like `"../../../important_directory"` would have caused a
recursive delete of an arbitrary directory outside the sandbox, not
just an unsafe write.

**Fix**: a new `_safe_generated_project_dir()` helper in `main.py`,
reusing `resolve_safe_path()` (the validator Experiments 066/067 built
and hardened for the write pipeline) — applied to all 6 `main.py`
sites, returning `None` for any unsafe or empty value, with every
caller updated to treat `None` as "not found" (matching each site's
existing behavior for a missing project).

A 7th, distinct case was found: `/deploy/github`, `/deploy/railway`,
`/deploy/cloudflare` accept `project_path` directly as a caller-supplied
parameter (not looked up from the database), and may legitimately be
absolute (matching how the frontend calls these endpoints with a path
from a prior generation response) — `resolve_safe_path()` rejects all
absolute paths unconditionally, so it doesn't fit this shape. A
narrower, containment-only check (`_require_contained_project_path()`)
was added instead: verifies the resolved path lands inside
`generated_projects/`, accepting either relative or absolute input,
rejecting anything outside.

`file_writer_service.py:523` itself: fixed by wrapping the existing
`resolve_safe_path()` call (already imported in that file from
Experiment 066) around `project_name`, raising `ValueError` on an
unsafe value — appropriate here since `write_files()`'s caller expects
a valid directory back, unlike the `main.py` sites' nullable pattern.

**Verified**: 8 regression tests covering all 7 sites plus the
dedicated `test_main_delete_job_path_helper_prevents_rmtree_escape`
test naming the exact severity finding above.

## Task 4 — CORS

**Before**: `allow_origins=["*"]` + `allow_credentials=True` — a
well-known anti-pattern combining a wildcard origin with credentialed
requests.

**After**: reads a comma-separated allowlist from `CORS_ORIGINS` (the
same env-var name this project's own `deployment_config_service.py`
already generates for *generated apps'* own CORS config — reused here
for naming consistency, not copied logic), defaulting to the local
dev frontend origins only (`http://localhost:5173`,
`http://127.0.0.1:5173`) when unset. Never a wildcard while
credentials are allowed, in either the default or the configured case.

**Verified**: 2 regression tests (default is not a wildcard, and
`CORS_ORIGINS` env var override actually changes the running app's
CORS configuration).

## Task 5 — Regression tests

`backend/tests/reliability/test_exp070_security_phase0.py` — 20 tests
covering all 4 tasks above (6 SECRET_KEY, 8 path-traversal, 2 CORS, 4
rate-limiting). All 20 pass. Full existing suite (49 files) re-run
before and after every change: zero regressions, with one pre-existing
test (`test_exp066_write_pipeline_hardening.py`) updated to compare
paths case-insensitively — a real, minor side effect of Task 3's fix
(`resolve_safe_path()`'s `Path.resolve()` call normalizes Windows path
casing differently than the old raw `os.path.join()`; a casing
difference only, not a behavior change) — and one confirmed
pre-existing, unrelated flakiness (`test_role_aware_journey.py`,
already documented as such in Experiment 067's own history).

## Cost

**$0.** No LLM generation, no canaries, no prompt changes, no
unrelated refactors. Every fix implements a confirmed finding from
Experiment 069's own security review (or, for the 6 additional
`main.py` sites and the deploy-endpoint containment check, a direct
extension of Task 3's explicit "audit every project path" instruction).
