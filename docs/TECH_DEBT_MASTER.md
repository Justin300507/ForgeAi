# ForgeAI Technical Debt Master List (Experiment 069, Part 10)

2026-07-12. Synthesized from every finding across this experiment's
four research forks plus prior experiments' still-valid findings
(Exp059/065's `docs/ENGINEERING_BACKLOG_50.md`, `docs/DEAD_CODE_REVIEW.md`).
Not a re-derivation of those — a ranked synthesis. Scores are 1-5
(5 = highest) unless noted; ROI = (Severity × Likelihood) ÷ Effort,
rounded, a heuristic ordering aid, not a precise formula.

| # | Item | Severity | Likelihood | Effort | ROI | Business impact | Developer impact | Source |
|---|---|---|---|---|---|---|---|---|
| 1 | Hardcoded insecure `SECRET_KEY` default (`app/dependencies/auth.py:13`) | 5 | 3 (only if env var unset) | 1 | **15** | Auth-forgery risk for any misconfigured deployment — disqualifying for commercial multi-tenant use as-is | Low — one-line fix, but requires a deploy-config audit to confirm no live deployment currently relies on the default | Exp069 Fork 4 + Fork 1 (independently corroborated) |
| 2 | `project_name` path-traversal gap (`file_writer_service.py:513-523`) | 4 | 2 (requires LLM to emit traversal text) | 1 | **8** | Structurally identical to the exact vulnerability class Experiments 066/067 spent two full cycles closing at the file level — closing it at the directory level is the natural next step | Low — reuses existing `resolve_safe_path()` infrastructure directly | Exp069 Fork 4 |
| 3 | No rate limiting anywhere (auth or generation endpoints) | 4 | 4 | 2 | **8** | Brute-force/credential-stuffing unmitigated; the most expensive endpoint in the system (`/project/v15`) has zero abuse protection | Low-Medium — a standard middleware addition | Exp069 Fork 4 |
| 4 | Redundant AST parsing across 12 validators (up to N×12 per verification pass) | 3 | 5 (fires on every generation) | 3 | **5** | Slower verification = slower generation = worse user-facing latency at scale | Medium — requires a shared-cache layer in front of 12 call sites | Exp069 Fork 4 |
| 5 | `endpoint_validator.py` has zero test coverage despite detecting the single largest failure cluster (`MissingEndpoint`, 48 instances) | 4 | 5 (already recurring) | 2 | **10** | This project's own biggest unresolved reliability gap has an untested detector — a regression here would be invisible until it hit production telemetry | Medium — needs dedicated test authorship | Exp069 Fork 3 |
| 6 | `JourneyCRUDFailure` has zero dedicated repair mechanism (30 instances, confirmed via full grep of `orchestrator.py`/`preflight.py`) | 5 | 5 | 3 | **8** | This project's own largest integration-stage failure cluster falls entirely to the generic, undifferentiated LLM repair loop | High — needs a new, purpose-built repair strategy (see Exp068's own #1 ROI recommendation: a deterministic auth-route completeness check) | Exp068 (carried forward) |
| 7 | 2 confirmed dead validator files (`import_validator.py`, `symbol_validator.py`, 147 lines) | 1 | 5 (already true) | 1 | **5** | None — purely a codebase-cleanliness issue | Low — confuses future readers into thinking these are active checks | Exp069 Fork 3 |
| 8 | `strategy_outcomes.json` (7-bucket) vs. `patterns.json` (21-pattern) taxonomies never reconciled | 2 | 5 | 2 | **5** | Repair-effectiveness measurement is split across two incompatible granularities — every future ROI analysis (like this one) has to work around this | Medium | Exp069 Fork 3 |
| 9 | Bare `except Exception` swallows deterministic-patcher crashes to a print statement (`orchestrator.py:1106`) | 3 | 3 | 1 | **9** | A silently-failing patcher looks identical to a patcher that correctly found nothing to fix — undermines trust in the whole prevention-rate metric | Low — narrow, well-scoped fix | Exp069 Fork 3 |
| 10 | Two parallel, independent benchmark systems (`run_benchmark.py` vs. `run_forgebench.py`) | 2 | 5 (both exist today) | 3 | **3** | Maintenance burden, unclear which is authoritative for any given measurement | Medium — a real consolidation decision, not a quick fix | Exp069 Fork 1 |
| 11 | Two frontend API-client files (`api.js` and `lib/api.js`) | 2 | 3 (unconfirmed if both are live) | 1 | **6** | Possible dead code, or worse, possible divergent behavior between two clients | Low-Medium — needs a quick trace of which is actually imported where | Exp069 Fork 1 |
| 12 | No unified CLI framework — ~8 independent standalone argparse scripts | 2 | 5 | 4 | **2.5** | Not a correctness risk, but a real onboarding/discoverability cost for anyone new to the project | Medium-High — genuinely more work than most items here | Exp069 Fork 1 |
| 13 | `main.py` is a 1477-line monolith with only 1 of ~46 routes going through `include_router()` | 2 | 5 | 4 | **2.5** | No functional risk today, but every new endpoint compounds the file's size and makes ownership/review harder | Medium-High | Exp069 Fork 1 |
| 14 | CORS `allow_origins=["*"]` + `allow_credentials=True` | 3 | 3 | 1 | **9** | A well-known anti-pattern widening cross-origin attack surface for the credentialed API | Low — a config change | Exp069 Fork 4 |
| 15 | `/api/download/{job_id}` has no ownership check (UUID-mitigated, not eliminated) | 2 | 2 | 2 | **2** | A leaked job_id URL lets anyone download that project regardless of owner | Low-Medium | Exp069 Fork 4 |
| 16 | `RepairRegistry` designed (has a test file) but never deployed to production | 1 | 5 (already true) | 3 | **1.7** | A half-finished subsystem is worse than either committing to it or cleanly retiring it — pure clarity debt | Low-Medium — a decision, not necessarily new code | Exp069 Fork 3 |
| 17 | `main.py`-only leftover LLM-authoring artifact comment in `session_validator.py` ("Add this check inside...") | 1 | 5 (already true) | 1 | **5** | None functionally — a documentation-hygiene smell suggesting other files may have similar artifacts | Low | Exp069 Fork 3 |
| 18 | No token revocation/blacklist mechanism for JWT auth | 2 | 2 | 3 | **1.3** | A compromised token remains valid for its full 30-minute window with no way to force-invalidate it | Low-Medium — a real feature addition (token blacklist store), not a one-line fix | Exp069 Fork 4 |
| 19 | `FORGE_DEPLOY_THRESHOLD` independently redefined in `core/context.py` and `scoring/engine.py` | 2 | 4 | 1 | **8** | Confirmed silent-divergence risk — a config change in one place may not propagate to the other | Low | Exp065 (carried forward, re-confirmed still present) |
| 20 | No `tests/` subdirectory maps 1:1 to most `app/` directories (topic- not module-organized) | 1 | 5 | 4 | **1.25** | Makes "does X have test coverage" a fuzzy question repo-wide, as this very experiment found repeatedly | Medium-High to fully reorganize; low to just document the gap | Exp069 Fork 1 |

## Top 5 by ROI (severity × likelihood ÷ effort)

1. **Endpoint-validator test coverage** (#5, ROI 10) — cheapest way to protect the detector for the project's single largest failure cluster.
2. **Bare-except patcher-crash swallowing** (#9, ROI 9) and **CORS misconfiguration** (#14, ROI 9) — tied, both narrow one-file fixes with real risk reduction.
3. **Hardcoded SECRET_KEY default** (#1, ROI 15, but not directly comparable to the others since its likelihood factor is conditional on misconfiguration) — the single highest-severity item on this list regardless of ROI-score ranking.
4. **`FORGE_DEPLOY_THRESHOLD` duplication** (#19, ROI 8) — cheap, already-known, still unfixed.
5. **`project_name` path-traversal gap** (#2, ROI 8) and **no rate limiting** (#3, ROI 8) — tied, both structurally important given this project's own recent two-cycle investment in exactly this class of fix.

## What this list deliberately does NOT include

Every item above was found through direct code investigation this
cycle or is a still-valid carry-forward from a prior experiment's own
documented finding — nothing here is speculative. Items considered but
excluded: broad "add more tests everywhere" (too vague to score
meaningfully — see item #20 for the one specific, scoped version of
this concern that IS included), "rewrite main.py's routing" (a design
decision for `docs/FORGEAI_V2.md`, not a debt-list item with a
standalone fix), and anything from `docs/RUNTIME_ROADMAP.md`'s
existing 20-item list (that list already covers runtime-failure-specific
debt in its own dedicated document — not duplicated here).
