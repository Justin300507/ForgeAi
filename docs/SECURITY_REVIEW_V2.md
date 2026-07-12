# ForgeAI Security Review V2 (Experiment 069, Part 8)

2026-07-12. Extends, does not repeat, three prior audits:
`docs/SECURITY_REVIEW.md` (Exp065 — path traversal in `write_files()`,
general `shell=True`/`eval`/`exec`/pickle/YAML sweep, hardcoded
secrets), `docs/WRITE_SECURITY.md`/`docs/WRITE_VALIDATION_MATRIX.md`
(Exp066/067 — exhaustive write-pipeline path-traversal/atomic-write/
symlink/Windows-drive/UNC audit). This document covers only the
categories those three did NOT cover.

## Finding #1 (CRITICAL) — Hardcoded insecure JWT signing key default

`app/dependencies/auth.py:13`:
```python
SECRET_KEY = os.environ.get("SECRET_KEY", "please-set-SECRET_KEY-env-var-in-production")
```
If any deployment runs without `SECRET_KEY` configured, **anyone can
forge a valid auth token for any user** (`create_access_token`, same
file, line 27, uses this key with zero runtime check that it was
actually overridden). Independently confirmed by two separate research
forks this cycle (system-atlas and security-delta), cross-corroborating
each other. **This is disqualifying for any multi-tenant commercial
deployment as-is** — see `docs/COMMERCIAL_READINESS.md` and
`docs/CTO_REVIEW.md`.

## Finding #2 (HIGH) — `project_name` path-traversal gap, structurally identical to what Experiments 066/067 spent two cycles fixing

`app/services/file_writer_service.py:513-517` only does
`.replace(" ", "_").lower()` on `project_name` before
`base_dir = os.path.join(_projects_root, project_name)` (line 523).
`project_name` originates from LLM output (the generation pipeline's
own project-naming step), not raw user input directly — but it is
**never validated against path-traversal characters at the directory
level**, the exact vulnerability class Experiments 066/067 hardened
for individual FILE paths via `resolve_safe_path()`. The attack
requires the LLM's naming step to emit `../`-laden text, a real but
non-trivial bar to clear — reported as a confirmed code-level gap, not
a confirmed exploited vulnerability. **The fix is a one-line reuse of
existing infrastructure**: apply `resolve_safe_path()` (or a
directory-specific variant) to `project_name` before it's joined into
`base_dir`.

## Finding #3 (HIGH) — No rate limiting anywhere in the application

Grepped the whole app for `slowapi`/`RateLimit`/`Limiter`/`rate_limit`
— the only hits are a keyword-matching reference in the generated-app
knowledge base (validating *generated* apps, not ForgeAI itself).
**`/login`, `/register`, and the expensive `/project/v15` generation
route all have zero request-rate protection.** Brute-force/
credential-stuffing against auth is unmitigated; nothing stops one
user from spamming the most expensive endpoint in the system.

## Finding #4 (MEDIUM) — CORS misconfiguration

`main.py:79-85`: `allow_origins=["*"]` combined with
`allow_credentials=True`. This is a well-known anti-pattern — combined
with credentialed requests, it substantially widens cross-origin
attack surface for the bearer-token API.

## Finding #5 (MEDIUM) — Unauthenticated download endpoint has no ownership check

`/api/download/{job_id}` (`main.py:1406-1412`) is **intentionally**
unauthenticated, per its own docstring ("it's the user's own generated
source"). Mitigated by `job_id` being a UUID (not sequentially
guessable), but there is still no check tying the download to the
requesting user — anyone with a leaked/logged job_id URL can download
that project regardless of who owns it.

## Finding #6 (LOW) — No token revocation

Auth is JWT bearer-token-based (`ACCESS_TOKEN_EXPIRE_MINUTES = 30`),
not cookie-based, so cookie-flag concerns don't directly apply. No
token revocation/blacklist mechanism found — a logged-out or
compromised token remains valid until its 30-minute natural expiry.

## Confirmed clean (re-verified fresh this cycle, not trust-and-repeat)

- **Uploads**: confirmed absent. No `UploadFile`/multipart endpoint
  exists anywhere in ForgeAI's own app — every match for these terms
  is either a knowledge-pattern keyword list (validating *generated*
  apps) or a `python-multipart` dependency-name reference in prompts.
- **Subprocess**: clean. All ~30 `subprocess.run`/`subprocess.Popen`
  call sites across 9 files use list-form arguments — **zero
  `shell=True`**, re-verified fresh. `github_service.py`'s
  LLM-influenced repo-name/description values flow into a
  `requests.post(..., json={...})` body, not a shell command or
  string-interpolated URL — safe.
- **Archive extraction: confirmed N/A, not just "not found."**
  `zip_service.py` only *creates* zips for download
  (`zipfile.ZipFile(..., "w", ...)`); grepped the entire codebase for
  `extractall`/`.extract(`/read-mode `ZipFile` — zero matches.
  ForgeAI never extracts an archive anywhere; zip-slip is not a live
  risk surface.
- **`eval`/`exec`**: zero found anywhere in `app/` (consistent with
  Exp065's original finding, re-confirmed).
- **Password handling**: bcrypt via `user_service.py:6-11`, correct —
  salted, `bcrypt.checkpw`.
- **No user-controlled SSRF surface found**: outbound requests are
  limited to LLM providers and deploy targets (GitHub/Cloudflare/
  Railway) with server-held tokens, not a user-supplied URL fetched
  server-side.

## Explicitly not exhaustively checked this cycle (flagged, not silently assumed clean)

- **Temporary files** beyond `atomic_write.py` (already audited in
  Experiments 066-067) — other `tempfile` usage sites not traced for
  permissions/predictable-name races.
- **Memory** — the 5,901-file `llm_cache/` directory's access pattern
  was not verified for whether it's ever loaded as a whole.
- **A full per-pattern ReDoS review** — only a narrow grep for
  nested-quantifier shapes (`(x+)+`) was run across
  `app/services/*.py` and `app/repair/*.py`, finding zero matches;
  this is a heuristic sweep, not exhaustive.
- **`get_current_user()` gating completeness** — not confirmed whether
  every state-mutating endpoint is gated or only some.

## Prompt injection / LLM output trust — summary of the single most novel finding

Beyond Finding #2 above (the concrete manifestation), the broader
question — does user "idea" text or LLM output ever reach a shell
command, an unsandboxed file path, or a raw SQL query — was traced and
found clean **except** for the `project_name` directory-path gap.
LLM-generated code becoming the generated app's own source is an
accepted, inherent part of this product's design (not a vulnerability
in itself); the risk surface specific to ForgeAI's OWN process is
narrower than it might first appear, and Finding #2 is the one
concrete instance found where that boundary is thinner than it should
be.
