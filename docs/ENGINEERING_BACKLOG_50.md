# Engineering Backlog — Next 50 Experiments (Experiment 065, Part 10)

2026-07-12. Every item below traces to a specific, cited finding in
`docs/ARCHITECTURE_REVIEW.md`, `docs/RELIABILITY_REVIEW.md`,
`docs/PERFORMANCE_REVIEW.md`, `docs/SECURITY_REVIEW.md`,
`docs/DEAD_CODE_REVIEW.md`, `docs/TEST_QUALITY_REVIEW.md`, or a
still-open item from Exp059's own backlog / Exp056-064's own flagged
follow-ups. No speculative fixes, no feature work. Format per item:
Title, Category, Effort (XS<1h / S 1-3h / M half-day-1day / L 2-4d / XL
1w+), Risk, API Cost, Expected ROI, Dependencies, one-line description.

---

**1. Fix `write_files`'s missing path-traversal guard**
Security | Effort: XS | Risk: Low | API Cost: $0 | ROI: High | Deps: none
Apply the exact same `..`/absolute-path guard `write_fix` already has (Exp060/064) to `file_writer_service.py::write_files` — the confirmed HIGH-severity gap in `docs/SECURITY_REVIEW.md` Finding #1.

**2. Wrap `engine.py::VerificationEngine.run()`'s subprocess cleanup in `finally`**
Reliability | Effort: S | Risk: Low | API Cost: $0 | ROI: High | Deps: none
Fixes the confirmed HIGH-severity backend-subprocess leak in `docs/RELIABILITY_REVIEW.md` §5 — an exception in stages 4-11 currently orphans a running uvicorn process.

**3. Fix `cloudflare_provider.py`'s Windows-broken temp-cleanup fallback**
Reliability | Effort: XS | Risk: Low | API Cost: $0 | ROI: Medium | Deps: none
Replace the hardcoded `/tmp/forge-cf-*` glob with `tempfile.gettempdir()`-relative logic so the defense-in-depth cleanup actually works on Windows (`docs/RELIABILITY_REVIEW.md` §6).

**4. Remove confirmed dead code in `backend_runner.py:421-431`**
Reliability | Effort: XS | Risk: Low | API Cost: $0 | ROI: Low | Deps: none
Unreachable `communicate()`/`return` block after an earlier `return` — verified dead, safe to delete.

**5. Write missing edge-case tests for `write_fix()`'s pre-existing guards**
Testing | Effort: S | Risk: Low | API Cost: $0 | ROI: High | Deps: none
Covers the empty-input, path-traversal, and `app/database.py`-special-case branches that Exp064's own 24 tests never touched (`docs/TEST_QUALITY_REVIEW.md`'s standout finding).

**6. Investigate whether `main.py`'s uvicorn runs single- or multi-worker in production**
Reliability | Effort: S | Risk: Low | API Cost: $0 | ROI: High | Deps: none
Resolves the real-vs-theoretical exploitability of the `cost_tracker.py` cross-thread state-contamination risk on the synchronous `/project/v15` route (`docs/ARCHITECTURE_REVIEW.md` Part 4 §1) — a pure investigation, not a fix.

**7. Fix `cost_tracker.py`'s cross-request state sharing on `/project/v15`**
Reliability | Effort: M | Risk: Medium | API Cost: $0 | ROI: High | Deps: #6
Once #6 confirms exploitability, either make `/project/v15` route through the same process-isolated queue path the worker model already uses, or scope `_session_calls`/`_run_totals` per-request instead of module-global.

**8. Consolidate `FORGE_DEPLOY_THRESHOLD`'s two independent definitions**
Architecture | Effort: XS | Risk: Low | API Cost: $0 | ROI: Medium | Deps: none
`app/core/context.py:283` and `app/scoring/engine.py:28` define the same constant independently — one importing the other closes a silent-divergence risk (`docs/ARCHITECTURE_REVIEW.md` Part 4 §2).

**9. Remove the 4 dead pre-v15 orchestrator endpoints (v8/v9/v10/v12)**
Architecture | Effort: S | Risk: Low | API Cost: $0 | ROI: Medium | Deps: none
333 combined lines confirmed reachable only through their own dead-end `main.py` handlers (`docs/DEAD_CODE_REVIEW.md`) — matches CLAUDE.md's own "only v15 is live" claim.

**10. Audit and reduce `main.py`'s 36 function-local imports**
Architecture | Effort: L | Risk: Medium | API Cost: $0 | ROI: High | Deps: none
76% of `main.py`'s dependencies are hidden, function-local imports — the exact shape that caused the confirmed Exp053→057 regression (`docs/ARCHITECTURE_REVIEW.md` Part 1). Requires care: some may be intentionally lazy for circular-import avoidance; each needs individual justification before hoisting.

**11. Build a formal replay-test fixture harness**
Testing | Effort: M | Risk: Low | API Cost: $0 | ROI: High | Deps: none
A shared `conftest.py`/fixtures library for the "extract real file, run check, assert before/after" pattern used 5 times by hand this cycle (Exp054/055/057/058/064) — `docs/TEST_QUALITY_REVIEW.md`.

**12. Add a performance-budget regression test for `validate_project()`'s scan count**
Testing | Effort: S | Risk: Low | API Cost: $0 | ROI: Medium | Deps: none
Tripwire test asserting the current ~20-`os.walk` count as a baseline, so it can't silently regress up (or be proven fixed) — `docs/TEST_QUALITY_REVIEW.md`, prerequisite for #21.

**13. Extend Exp064's semantic write check to cover `regenerate_arch`/`regenerate_module`**
Repair | Effort: M | Risk: Medium | API Cost: $0 | ROI: High | Deps: none
Confirmed gap: `app/repair/orchestrator.py:866`'s reverse-layer call into `generate_project_v6` writes files via a different mechanism than `write_fix`, so Exp064's guard doesn't cover it (`docs/ARCHITECTURE_REVIEW.md` Part 1).

**14. Consolidate `validator_service.py`'s ~20 redundant `os.walk` calls**
Performance | Effort: L | Risk: Medium-High | API Cost: $0 (+ optional small live canary to confirm) | ROI: High | Deps: #12
One shared file-list computed once, passed to all delegated validators — highest-impact unfixed performance finding from Exp059, reconfirmed still accurate.

**15. Consolidate `deterministic_patcher.py`'s 21 separate `rglob()` calls**
Performance | Effort: L | Risk: Medium-High | API Cost: $0 | ROI: Medium-High | Deps: none
Same shape as #14, smaller scope.

**16. Drop the duplicate `compute_prevention_rate` call in `/observatory`**
Performance | Effort: XS | Risk: Low | API Cost: $0 | ROI: Low-Medium | Deps: none
Confirmed-unused duplicate computation (`docs/PERFORMANCE_REVIEW.md`).

**17. Cache or tail-read `experiments.md` parsing for Observatory**
Performance | Effort: S-M | Risk: Low | API Cost: $0 | ROI: Medium (growing) | Deps: none
File is now 4476 lines (up from 4074 at last measurement) and re-parsed in full on every `/observatory` request — cost grows with every future experiment.

**18. Cache `failure_memory._load()` within one process lifetime**
Performance | Effort: S | Risk: Low | API Cost: $0 | ROI: Low | Deps: none
Small file, but reloaded 2+ times per generation with no memoization.

**19. Dedupe the two regex pairs in `database_patcher.py`**
Performance | Effort: XS | Risk: Low | API Cost: $0 | ROI: Low | Deps: none
Cosmetic, negligible performance impact, pure cleanliness.

**20. Decompose `run_user_journey` (worst complexity/depth in the repo: 166/15)**
Architecture | Effort: XL | Risk: High | API Cost: small live canary to confirm behavior preservation | ROI: Medium-High | Deps: comprehensive pre-refactor test coverage
Highest raw complexity function in the codebase; also the least-documented directory (`app/runtime/`, 25.6% docstring coverage) — risk and doc-gap overlap exactly.

**21. Decompose `generate_project_v6` (highest-risk function in the repo)**
Architecture | Effort: XL | Risk: High | API Cost: mandatory live canary re-validation | ROI: High | Deps: #20 (as a lower-stakes trial run first)
Already produced one confirmed regression (Exp053→057); still the single highest-consequence function to touch carelessly.

**22. Write a current-state generator-pipeline doc + diagram**
Developer Experience | Effort: M | Risk: none | API Cost: $0 | ROI: Medium-High | Deps: none
No doc describes the live planner→architect→backend/frontend flow as it actually exists — only an aspirational redesign proposal exists (`docs/FORGEAI_VNEXT_REPORT.md`).

**23. Write a narrative developer-onboarding doc**
Developer Experience | Effort: M | Risk: none | API Cost: $0 | ROI: Medium | Deps: none
`CLAUDE.md` is reference-oriented; nothing walks a new engineer through the codebase or a first change.

**24. Add mermaid diagrams for the 13+ undiagrammed subsystems**
Developer Experience | Effort: L (can be split per-subsystem) | Risk: none | API Cost: $0 | ROI: Medium | Deps: #22
Only 2 of 29 docs have a diagram (`REPAIR_ARCHITECTURE.md`, `REPAIR_GRAPH.md`) — generation, validation, verification, scoring, retry, deployment, queue, memory, Observatory, providers, contract, confidence, design, knowledge all have none.

**25. Verify/update `docs/V16_DEPLOYMENT_RELIABILITY_AUDIT.md`'s staleness**
Deployment | Effort: S | Risk: none | API Cost: $0 | ROI: Medium | Deps: none
Titled "V16" — plausibly out of date relative to current deployment code; not re-verified this cycle.

**26. Raise docstring coverage in `app/runtime/` and `app/verification/`**
Developer Experience | Effort: M | Risk: Low | API Cost: $0 | ROI: Medium | Deps: none
The two weakest-documented directories (25.6%, 33.3%) are also the two highest-risk (worst complexity function, Exp060's extension point + confirmed subprocess leak).

**27. Migrate the remaining 8 `validator_service.py`-internal validators to the Diagnostic contract**
Validation | Effort: M | Risk: Low-Medium | API Cost: $0 (+ optional live confirmation, per Exp061's own precedent) | ROI: Medium | Deps: none
Exp060 migrated 15 of 24 total validator functions; the remaining 8 (`validate_backend_imports`, `validate_imported_symbols`, `validate_frontend_imports`, `validate_frontend_nav_targets`, `validate_frontend_api_client`, `validate_route_quality`, `validate_requirements`, `validate_common_antipatterns`) are fully functional via the legacy fallback but not yet contract-native.

**28. Add per-validator timing instrumentation**
Observability | Effort: S | Risk: Low | API Cost: $0 | ROI: Medium | Deps: none
None of the 13 (now partially migrated) standalone validators time themselves, unlike `verification/engine.py`'s consistent convention — flagged originally in Exp059's `docs/VALIDATOR_REVIEW.md`, still open.

**29. Add minimal logging to the still-silent validators**
Observability | Effort: S | Risk: Low | API Cost: $0 | ROI: Medium | Deps: none
Same root finding as #28 — zero log output makes a stuck validation undebuggable.

**30. Consolidate `_COLUMN_TYPE_RULES`/`_SCHEMA_FIELD_TYPE_RULES`'s confirmed drift**
Repair | Effort: S-M | Risk: Low-Medium | API Cost: $0 | ROI: Medium-High | Deps: none
Exp059 found these two rule tables have already drifted apart (one has a `"value"` field-name suffix the other lacks) — a live, unfixed bug, not just duplication.

**31. Investigate the ranked LLM-behavior hypotheses from Exp063**
LLM Quality | Effort: M | Risk: Low | API Cost: small (a few cache-bypassed generations needed) | ROI: High | Deps: none
Exp063 confirmed the auth-corruption mechanism but left 3 undistinguished hypotheses for WHY the LLM produces it (prompt framing, training-data prior, cache-replay artifact) — resolving this could inform a genuine prompt-level fix (out of scope for offline cycles, in scope for a dedicated LLM-quality cycle).

**32. Extend the semantic write check to SQLAlchemy model attribute access**
Repair | Effort: M | Risk: Low-Medium | API Cost: $0 | ROI: Medium | Deps: none
Same algorithm as Exp064's Pydantic check, applied to `Column()`/`mapped_column()`-declared fields — flagged as a "future extension" in `docs/EXP064_SEMANTIC_VALIDATION.md`.

**33. Resolve simple cross-file imports for the semantic write check**
Repair | Effort: M | Risk: Medium | API Cost: $0 | ROI: Low-Medium | Deps: #32
Bounded, single-hop `from app.schemas.x import Y` resolution — widens Exp064's coverage without becoming a general import-graph walker.

**34. Rotate which canary app gets "repeat validation" instead of defaulting to `todo`**
Testing | Effort: XS (process change) | Risk: none | API Cost: ongoing (same as current canary spend) | ROI: High | Deps: none
Exp062's own finding: 6 combined `todo`/`blog_cms`-repeat live rounds never triggered a legacy-validator fallback, while 2 of 3 fresh apps did in one cycle — `todo` alone under-represents real-world validator/fallback activation.

**35. Fix Observatory's per-app timeline averaging**
Observability | Effort: S-M | Risk: Low | API Cost: $0 | ROI: Medium | Deps: none
`compute_reliability_timeline` averages all apps in a canary run together — a per-app regression (like `todo`'s 99.71→74.4 drop) can be invisible if other apps move the opposite direction the same round.

**36. Add cost/duration trend to Observatory**
Observability | Effort: S | Risk: Low | API Cost: $0 | ROI: Low-Medium | Deps: none
`generation_log.jsonl` already has `total_cost_usd`/`total_llm_time_s` per entry; no trend view exists for either.

**37. Add a table-view accessibility fallback to Observatory's trend chart**
Observability | Effort: S | Risk: none | API Cost: $0 | ROI: Low | Deps: none
Flagged in Exp059's own Part 4 finding — this project's own `dataviz` skill guidance requires this for any chart with ≥2 series.

**38. Add manual refresh / polling to the Observatory page**
Observability | Effort: XS | Risk: none | API Cost: $0 | ROI: Low | Deps: none
Currently fetches once on mount; a finished canary run requires a full page reload to see.

**39. Complete the unused-imports sweep (10-file random sample)**
Developer Experience | Effort: S | Risk: Low | API Cost: $0 | ROI: Low | Deps: none
Explicitly not completed in `docs/DEAD_CODE_REVIEW.md` — genuinely open, not low-value, just unfinished.

**40. Investigate `app/queue/worker.py`'s `GenerationContext` memory retention across jobs**
Performance | Effort: S | Risk: Low | API Cost: $0 | ROI: Low-Medium | Deps: none
Flagged as `Unknown` in `docs/PERFORMANCE_REVIEW.md` — does a long-running worker process accumulate completed contexts in memory, or discard per-job? Needs live profiling to fully resolve, but static tracing can narrow it first.

**41. Add explicit timeout to the ~14 HTTP call sites, audit each**
Reliability | Effort: S | Risk: Low | API Cost: $0 | ROI: Medium | Deps: none
`docs/SECURITY_REVIEW.md` confirmed zero `verify=False` (good) but didn't check timeout coverage on the same 14 sites — cross-reference with `docs/RELIABILITY_REVIEW.md`'s scope note.

**42. Add a lock (or lock-free CAS pattern) around `ai_provider.py`'s cooldown dict**
Reliability | Effort: S | Risk: Low | API Cost: $0 | ROI: Low | Deps: none
Closes the narrow, self-limiting race window found under `parallel_backend_service.py`'s thread pool (`docs/RELIABILITY_REVIEW.md` §8) — low severity but a clean, cheap fix.

**43. Correct `backend_runner.py`'s health-check-loop timing comment**
Reliability | Effort: XS | Risk: none | API Cost: $0 | ROI: Low | Deps: none
Claims "15s," actual worst case ~75s — a one-line comment fix, but prevents a future engineer from mis-tuning based on the wrong number.

**44. Design a job-cancellation mechanism**
Architecture | Effort: L | Risk: Medium | API Cost: $0 for design, live testing for validation | ROI: Medium | Deps: none
Confirmed zero cancellation support anywhere (`docs/RELIABILITY_REVIEW.md` §3) — a real product gap for a commercial release where users expect to be able to stop a running job.

**45. Investigate provider-failure logging for secret leakage across all SDK versions**
Security | Effort: S | Risk: Low | API Cost: $0 | ROI: Low-Medium | Deps: none
`docs/SECURITY_REVIEW.md` Finding #12 — Low severity, but "Unknown whether this reaches an external-facing sink" deserves closing out.

**46. Complete the unsafe-temp-files security sweep**
Security | Effort: S | Risk: Low | API Cost: $0 | ROI: Low-Medium | Deps: none
Explicitly not completed in `docs/SECURITY_REVIEW.md` §3 — genuinely open.

**47. Evaluate sandboxing options for the default (non-Docker) code-execution path**
Security / Architecture | Effort: XL (research spike first) | Risk: N/A (a design decision) | API Cost: $0 for research | ROI: High (long-term) | Deps: none
`docs/SECURITY_REVIEW.md`'s architectural finding — the single most important pre-commercial-release security decision, deliberately not treated as a quick fix given its scope.

**48. Write an integration test suite spanning `deterministic_patcher` + `engine.py` + `app/repair/orchestrator.py`**
Testing | Effort: L | Risk: Low | API Cost: $0 | ROI: Medium | Deps: #11 (the replay harness makes this easier)
Confirmed gap: only one cross-subsystem integration test exists in the whole suite (`docs/TEST_QUALITY_REVIEW.md`).

**49. Investigate property-based testing (hypothesis) for the AST-based validators/checks**
Testing | Effort: M (pilot on 1-2 functions first) | Risk: Low | API Cost: $0 | ROI: Low-Medium | Deps: none
Zero property-based tests exist anywhere; the AST-heavy validator/semantic-check functions (Exp060, Exp064) are natural candidates — random valid/invalid Python snippets could surface edge cases example-based tests miss.

**50. Consolidate `render.yaml` vs. `render_provider.py` config duplication**
Deployment | Effort: S | Risk: Low | API Cost: $0 | ROI: Low-Medium | Deps: none
Carried forward from the prior Deployment Reliability Audit (project memory) — a small, still-unaddressed config-duplication cleanup, not an active bug.

---

## Sort key note

Items are grouped roughly by theme/dependency chain above, not strictly
by ROI — see the final summary's "Top 20 highest ROI" / "Top 10 $0" /
"Top 10 worth Cerebras credits" lists for ROI-first orderings drawn from
this same 50.
