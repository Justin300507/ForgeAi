# ForgeAI Commercial Readiness Assessment (Experiment 069, Part 11 — Security updated by Experiment 070, Reliability re-measured 2026-09-24)

2026-07-12. Pretend ForgeAI launches tomorrow. Every score below cites
the specific evidence behind it — drawn from this experiment's own
research (Parts 1-9) plus Experiment 068's still-current runtime data.
Scores are 1-10, 10 = fully commercial-grade.

**2026-09-24 update (Experiment 159 + live re-measurement)**: the
**Reliability** row's headline number is dramatically different from
this document's original reading and needs re-stating honestly rather
than silently left stale. Re-ran `compute_observatory()` (the same
function this document originally cited) live against the current
`failure_memory/generation_log.jsonl` and `benchmark_results/
canary_history.json`:

```
first_try_success_rate: 86.7%  (trend +6.7 vs prior window, confidence: High)
top_failure_now:        JourneyCRUDFailure  (same category, much rarer)
canary_health:          Healthy  (todo 99.7, blog_cms 98.5, crm 96.2, 2026-09-23)
auth_completeness:      100% (30/30, window=30)
generation_success_rate: 90.0%
deploy_rate:            40.0%  -- still the weakest number here, see caveat below
```

This is not a re-interpretation of old data — it is the literal
30-generation trailing window as of today, up from the 30% (and
declining) figure Experiment 069 cited as "the single most damning,
most evidenced number in this entire review." The gap is explained by
real, cited work across the two months between: the MissingEndpoint
architecture-repair grounding fix (2026-09-08, validated 84.8% success
on a 46-app run), and this session's own two root-caused fixes
(Experiment 159: a regex gap that silently defeated an existing
Update-schema patcher whenever the field had a trailing comment, and a
new patcher for models whose primary key isn't named `id`), each
verified against the actual previously-broken generated projects and a
clean live 3-app canary.

**Caveats, so this reads as evidence rather than a victory lap:**
- The window is 30 generations, not the 100+ Experiment 069's original
  number was drawn from — real, but a smaller and more recent sample.
- `deploy_rate: 40%` is unchanged and still low — reliability
  (does the app work) and deploy success (does it actually ship) are
  different measurements, and this document's original deploy caveat
  ("canaries run with `--no-deploy` by convention... rarely exercised")
  still applies verbatim.
- **The Railway production deployment (the one this document and the
  README describe as canonical) has had zero active instances since
  2026-07-31** — not a code problem: `railway up` fails immediately
  with "Your trial has expired. Please select a plan to continue using
  Railway." This is a billing decision, not something fixable in this
  session. The Render fallback deployment (`docs/`'s own prior-session
  notes; `forgeai-5nw7.onrender.com`) is confirmed live and serving
  `/health` as of today, but was last known-updated 2026-07-29 and had
  never received any of the ~27 commits (including this session's two
  fixes) sitting unpushed locally until today — now pushed to
  `origin/main`, which should trigger its auto-deploy, though this
  session had no Render dashboard/API credential to directly confirm
  the deploy fired or to verify `FORGE_PIPELINE_VERSION=v15` is set
  there the way it's confirmed set on Railway.
- 86.7% is a measurement of the generation pipeline in a controlled
  canary/batch context (OpenAI provider, `--no-deploy`, this machine).
  It is evidence the *code* is far more reliable than in July, not a
  guarantee of what a live public user's specific prompt will score.

**2026-07-12 update (Experiment 070)**: the Security row below was
re-scored after Security Phase 0 closed all 5 launch blockers this
document originally cited (`docs/SECURITY_PHASE0.md`). All other rows
are unchanged from Experiment 069's original assessment — none of
those findings were in scope for Phase 0.

| Category | Score | Evidence |
|---|---|---|
| **Reliability** | **~~3/10~~ → 7/10** | **Re-measured 2026-09-24.** `first_try_success_rate` is now 86.7% (30-generation trailing window, trend +6.7, confidence High) versus 30%-and-declining originally. `auth_completeness` is 100% (30/30) — the exact gap Phase 1 (items 075-084) was meant to close, apparently since closed. `top_failure_now` is still `JourneyCRUDFailure`, but far rarer, and this session root-caused and fixed two of its concrete instances live (Experiment 159). Not an 8+: the window is smaller than the original 100+-generation read, `deploy_rate` is still only 40% (see Deployment row, unchanged), and this measures the pipeline in a controlled canary context, not real public-user prompt diversity. |
| **Security** | **~~5/10~~ → 8/10** | **Updated by Experiment 070.** All 5 originally-cited blockers closed: hardcoded `SECRET_KEY` default now fails startup instead of silently accepting a known-insecure value; rate limiting added to auth/generation/deploy endpoints; CORS wildcard+credentials fixed to an explicit allowlist; project-path traversal fixed — and found to be a **broader** problem than originally cited (7 sites total, not 1, including two `shutil.rmtree()` call sites in `delete_job`/`delete_all_jobs` that were the single most severe finding of the whole Phase 0 cycle). 20 new regression tests, full existing suite (49 files) still green. Not a 10/10: token revocation, `/api/download` ownership checks, and full auth-gating-completeness verification remain open (`docs/LAUNCH_SECURITY_CHECKLIST.md`'s "still open" section) — real, but non-blocking gaps for a closed beta, unlike the 5 that were fixed. |
| **Scalability** | **4/10** | The synchronous `/project/v15` route shares module-global `cost_tracker.py` state across concurrent requests via Starlette's threadpool (Exp065's still-valid finding) — a real cross-request contamination risk under concurrent load. Zero rate limiting compounds this. The async job-queue path (V19) is architecturally sounder (separate OS processes, confirmed correctly-scoped) but isn't the default entry point. |
| **Developer Experience** | **4/10** | No unified CLI (8 independent standalone scripts, `docs/SYSTEM_DESIGN.md` §11); `main.py` is a 1477-line monolith with only 1 of ~46 routes using `include_router()`; zero module-level docstrings across all 14 validator files (`docs/VALIDATOR_INTELLIGENCE.md`). A new engineer has real friction finding "where does X live." |
| **Documentation** | **6/10** | Split verdict: this project's own retrospective/audit documentation (now 30+ files in `docs/`, most written across Experiments 059-069) is unusually thorough and evidence-disciplined for a project this size. But forward-looking developer documentation (module docstrings, a README-style onboarding path, API reference) is comparatively thin — the documentation that exists is mostly "what happened," not "how do I work on this." |
| **Maintainability** | **4/10** | 90 deterministic patcher functions across 3 files with inconsistent registration patterns (`preflight.py`'s clean decorator registry vs. `deterministic_patcher.py`'s ad-hoc functions, `docs/REPAIR_INTELLIGENCE.md`); confirmed dead code (2 validator files, 147 lines); two parallel benchmark systems; two incompatible repair-outcome taxonomies never reconciled. |
| **Testing** | **5/10** | A genuinely large test suite exists (48+ files confirmed still passing as of this session's own Experiments 066/067 full-suite runs) — but coverage is uneven in a way that matters: `endpoint_validator.py`, the detector for the single largest failure cluster, has **zero dedicated test coverage** (`docs/VALIDATOR_INTELLIGENCE.md`). Coverage is topic-organized, not module-organized, making systematic gap-finding hard. |
| **Deployment** | **4/10** | The deploy-provider architecture itself (`app/deployments/`, a clean ABC pattern) is one of the better-designed subsystems in the codebase (`docs/SYSTEM_DESIGN.md` §5) — but Experiment 068's own variance-report data shows `deployment_success 0/51 passed (0%)` historically, and canaries run with `--no-deploy` by convention, meaning the deploy path is rarely exercised in this project's own measurement discipline. Good architecture, unproven at the numbers level. |
| **Observability** | **8/10 — the strongest category by a clear margin** | Observatory, `generation_log.jsonl`, `canary_history.json`, forensic failure bundles, and `app/memory/reliability_metrics.py`'s well-designed, single-source-of-truth compute functions together form a genuinely distinctive telemetry system — this entire experiment (and Experiment 068 before it) leaned on this infrastructure heavily and it held up well. The one real gap: only 1 of 87 `generation_log.jsonl` entries references any of the 14 forensic bundles — the bundle system and the generation log aren't fully wired together yet. |
| **Upgradeability** | **4/10** | Ten historical generation-pipeline versions (`/project/v6` through `/v14`) remain registered alongside the live `/v15` — an upgrade path exists but old versions aren't cleanly retired. `AppContract` (this project's own stated "priority 1" architectural upgrade vehicle) remains "a newer, partially-adopted subsystem" per its own prior inconclusive-evaluation history, not yet the load-bearing IR it was designed to become. |
| **Supportability** | **6/10** | The extensive telemetry (same infrastructure praised under Observability) makes debugging a production issue unusually tractable for a project this size — a real strength most comparable-scale projects lack. Capped below Observability's score because of the bundle↔log linkage gap noted above: today, most of the richest failure evidence (13 of 14 forensic bundles) isn't reachable by querying the generation log, meaning a support engineer following the "normal" telemetry path would miss most of it. |

## Weighted overall commercial-readiness read

No single formal weighting is asserted here (that would imply false
precision) — but reading the table qualitatively: Observability (8/10)
is still the strongest asset, but as of 2026-09-24 it is no longer
alone — **Reliability moved from the worst-scored row to a
mid-pack 7/10**, the single biggest change in this document's history.
The remaining lowest scores (Scalability 4/10, Developer Experience
4/10, Maintainability 4/10, Deployment 4/10, Upgradeability 4/10) are
architecture/process gaps, not correctness gaps — a different, less
urgent class of problem than "does the generated app work."

## Explicit answer: is ForgeAI ready for a public beta today?

**2026-09-24 update: reasoning has changed again.** The original two
blockers were (1) the security gap and (2) the 30%-and-declining
first-try success rate. **Both are now closed** — (1) per Experiment
070, unchanged since; (2) per the 86.7% re-measurement above,
Experiment 159's two live fixes, and `auth_completeness` at 100%
(Phase 1's recommended item, apparently already done). **This changes
the honest answer from "closed beta only" to: a closed/invite-only
beta is ready now, and a cautious general-availability beta is
reasonable once two remaining, non-code items are addressed:**

1. **Production is actually down.** The Railway deployment this
   document and the README call canonical has had zero running
   instances since 2026-07-31 — Railway's trial expired and `railway
   up` now refuses with "select a plan to continue." This is a
   billing decision only the account owner can make; a fallback Render
   deployment is alive but was two months stale until this session
   pushed the outstanding local commits to `origin/main` moments ago
   (unconfirmed whether its auto-deploy has picked them up, or whether
   `FORGE_PIPELINE_VERSION` is set there — no Render credential was
   available this session to check).
2. **`deploy_rate` is still 40%.** Reliability answers "does the
   generated app work"; this answers "does shipping it work," and
   canaries still run with `--no-deploy` by convention, meaning this
   number is rarely exercised by this project's own measurement
   discipline. Worth a dedicated pass before leaning on "deploy my app"
   as a headline feature for new users.

Neither of these is a reliability regression — the pipeline itself is
in the best-measured state this document has ever recorded. They are
"is the lights-on infrastructure actually on" and "does the last mile
work," which is a different, narrower punch list than the one this
document opened with.
