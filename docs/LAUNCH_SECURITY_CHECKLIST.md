# Launch Security Checklist (Experiment 070)

2026-07-12. What an operator must do before deploying ForgeAI, and
what's still open after Security Phase 0. Not a general security
audit — a launch gate, derived from Experiments 069-070's specific
findings.

## Before every deployment (required)

- [ ] **`SECRET_KEY`** is set to a strong, random value — generate
  with `python -c "import secrets; print(secrets.token_hex(32))"`.
  The app will now refuse to start without this (Task 1) — this is
  enforced automatically, not just a recommendation.
- [ ] **`CORS_ORIGINS`** is set to your actual frontend domain(s),
  comma-separated (e.g. `https://app.yourdomain.com`). If unset, the
  app defaults to `localhost`-only origins — correct for local dev,
  **wrong for any real deployment** (your production frontend will be
  blocked by CORS if you forget this).
- [ ] Confirm rate limits are appropriate for your expected traffic
  (`docs/SECURITY_PHASE0.md`'s table) — the defaults (5/60s auth,
  10/60s generation, 10/60s deploy) are tuned for a small/early-stage
  deployment, not necessarily a high-traffic one.
- [ ] Confirm you're running a **single backend process**. The rate
  limiter (Task 2) is in-memory and single-process-only — under
  multiple uvicorn workers, the effective rate limit multiplies by
  worker count. If you scale to multiple processes, this needs
  revisiting first (`docs/ROADMAP_100_EXPERIMENTS.md` item 113 covers
  the related scalability work).

## Verify before considering a deployment "launched"

- [ ] Run `backend/tests/reliability/test_exp070_security_phase0.py`
  directly against your deployment target's Python environment — all
  20 tests should pass.
- [ ] Confirm `.env` (or your deployment platform's secrets manager)
  actually contains `SECRET_KEY` — a missing key now crashes the app
  at startup with a clear error, so this will be obvious if you
  forgot it, but confirm anyway rather than finding out at 2am.
- [ ] If you use the `/deploy/github`, `/deploy/railway`, or
  `/deploy/cloudflare` endpoints programmatically (not just via
  ForgeAI's own frontend), confirm your `project_path` values are
  either relative (`generated_projects/<name>`) or an absolute path
  that genuinely lives under your `generated_projects/` directory —
  anything else is now rejected with a 400, by design (Task 3).

## What Phase 0 fixed (no longer launch blockers)

1. Hardcoded insecure `SECRET_KEY` default — closed.
2. Missing rate limiting on auth/generation/deploy — closed.
3. `project_name`/`project_path` traversal (7 sites, including 2
   `shutil.rmtree()` call sites — the most severe finding of this
   cycle) — closed.
4. CORS wildcard + credentials misconfiguration — closed.
5. Missing security regression tests — closed (20 new tests).

## What is still open (not blockers for an invite-only beta, but not fully closed either)

- **Token revocation**: no JWT blacklist/revocation mechanism exists.
  A compromised token remains valid for its full 30-minute window.
  (`docs/SECURITY_REVIEW_V2.md` Finding #6, `docs/ROADMAP_100_EXPERIMENTS.md`
  item 112 — not attempted this cycle, out of Phase 0's explicit scope.)
- **`/api/download/{job_id}` ownership check**: mitigated by `job_id`
  being a UUID (not sequentially guessable), but a leaked URL still
  lets anyone download that project regardless of owner.
  (`docs/ROADMAP_100_EXPERIMENTS.md` item 074 — a Phase 0 item that
  was scoped into the original 5 named blockers' adjacent findings but
  not explicitly re-verified as fixed or open this cycle; treat as
  **open** until separately confirmed.)
- **`get_current_user()` gating completeness**: not confirmed this
  cycle whether every state-mutating endpoint requires authentication.
  (`docs/ROADMAP_100_EXPERIMENTS.md` item 159.)
- **Password strength validation** on `/register`: not confirmed to
  exist. (`docs/ROADMAP_100_EXPERIMENTS.md` item 168.)
- **Rate limiter is single-process only** (documented above, not a
  bug — a scoped tradeoff for the $0/simple-implementation constraint
  of this cycle).

## What a genuinely production-scale (not invite-only-beta) launch would still need

Everything in the "still open" section above, plus the broader
scalability work in `docs/ROADMAP_100_EXPERIMENTS.md` Phase 5
(`cost_tracker.py`'s module-global state under concurrent requests,
item 110) — Security Phase 0 makes ForgeAI's security posture
no-longer-a-launch-blocker for a closed/invite-only beta specifically,
not a statement that every security or scalability concern is
resolved for a general-availability commercial launch.
