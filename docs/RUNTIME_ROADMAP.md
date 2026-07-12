# Runtime Failure Roadmap (Experiment 068, Parts 6 &amp; 8)

2026-07-12. Part 6 ranks the 15 traced clusters by frequency, severity,
repairability, and engineering cost. Part 8 recommends 20 concrete
runtime fixes ordered by ROI. **This document recommends; it does not
implement anything** — per this experiment's own rule.

Ranking methodology note, stated up front rather than hidden in a
footnote: frequency and first/last-seen dates are hard data (`patterns.json`).
Severity is a judgment call informed by stage (generation/build/runtime
failures that crash or block the app entirely are ranked above
integration-stage partial failures) and directly cited evidence (e.g.
"never once True across 16 canary runs" is evidence, not opinion).
Repairability and engineering cost are estimates informed by what
detection/repair infrastructure `docs/RUNTIME_KNOWLEDGE_BASE.md`
confirmed already exists (extending existing infrastructure is cheaper
than building new) — these two columns are the most judgment-dependent
in this document and are marked as such.

## Part 6 — Cluster ranking

| Rank | Cluster | Frequency (count) | Severity | Repairability (current) | Est. engineering cost to close remaining gap |
|---|---|---|---|---|---|
| 1 | MissingEndpoint | 48 (highest) | HIGH — endpoint simply doesn't exist, hard failure | Detection: solid. Repair: LLM-only, no deterministic fallback | MEDIUM — needs entity-aware CRUD templates, broader than one route |
| 2 | JourneyCRUDFailure | 30 | HIGH — blocks the entire user journey; `todo`'s crud_ok has never been True in 16 canary runs | Detection: runtime-only (inherent). Repair: **zero dedicated mechanism**, confirmed | LOW for the dominant 64% sub-cause (reuse the existing static auth template); MEDIUM for the rest |
| 3 | AttributeError | 18 | HIGH — crashes at runtime | Detection: partial. Repair: 3+ patchers already, pattern persists (long-tail) | HIGH — diminishing returns already visible; needs triage before more investment, not blind patching |
| 4 | ImportError | 13 | HIGH — crashes at startup | Detection: none found. Repair: narrow only | MEDIUM |
| 5 | ConfigAttributeError | 13 | HIGH when it occurs, but likely already-closed | Detection: none (runtime-only). Repair: dedicated, well-documented, confirmed live | LOW — mostly a re-verification task, not new engineering |
| 6 | SQLAlchemyError | 11 | HIGH — crashes at startup | Detection: solid. Repair: extensive, confirmed firing | LOW — already well-covered, stale last_seen |
| 7 | RouterExportMismatch | 9 | HIGH — crashes at startup (wrong import) | Detection: Unknown (possibly combined with repair). Repair: 3 patchers | LOW — mostly a documentation/clarification task |
| 8 | ModuleNotFoundError | 9 | HIGH — crashes at startup | Detection: none found. Repair: narrow only | MEDIUM |
| 9 | FrontendBuildError | 7 | HIGH — app never loads at all | Detection: inherent (the build IS the check). Repair: multiple patchers, stale last_seen | LOW — already well-covered |
| 10 | SyntaxError | 6 | HIGH — file unusable | Detection: confirmed solid (`ast.parse()`, verified this session). Repair: inline auto-repair | LOW — mostly a re-verification task (is 07-11's instance a new bug or the old one recurring) |
| 11 | NoReferencedTableError | 6 | HIGH when it occurs, but durably fixed (stalest last_seen of any cluster) | Detection/repair: shared with SQLAlchemyError, solid | VERY LOW — no further investment justified by current evidence |
| 12 | PydanticSerializationError | 5 | MEDIUM — response fails to serialize, degrades one endpoint, not the whole app | Detection: none found. Repair: 4+ patchers | MEDIUM — detection gap is the real remaining work |
| 13 | NotNullViolationError | 4 | HIGH — IntegrityError blocks writes | Detection/repair: combined, historically significant fix (Exp012) | LOW |
| 14 | ValidationError | 3 | MEDIUM — Unknown severity pending taxonomy disambiguation | Unknown — possible overlap with #12 | LOW to investigate, Unknown to fix until disambiguated |
| 15 | RelationshipModelNotImported | 3 | HIGH when it occurs, but durably fixed (never recurred since 2026-06-22) | Detection/repair: shared with SQLAlchemyError family | VERY LOW — no further investment justified |

## Part 8 — Top 20 runtime fixes, ordered by ROI

ROI here means: (how much of the measured failure volume this closes) ×
(how directly the evidence supports it) ÷ (how much new engineering it
requires, given what already exists). The single strongest cross-cutting
finding shaping this list: **once a generation needs a 2nd repair
attempt, it succeeds only 3% of the time (1/33), and 0% by the 3rd
attempt (0/14)** — from `generation_log.jsonl`'s 87 real runs (see
`docs/RUNTIME_FAILURE_CLUSTERS.md` Part 3). This means fixes that
prevent a failure from ever reaching the repair loop (generation-time
or preflight fixes) are worth categorically more than improvements to
the repair loop's later attempts, which the data shows essentially
never pay off. This principle is the primary tiebreaker below.

1. **Deterministic auth-route completeness check at generation/preflight time, enforcing the existing known-good static auth template.** Targets the single largest, most concrete, most recent piece of evidence in the whole study: 9 of 14 forensic bundles (64%) are `POST /auth/register` returning 404. ForgeAI already has a provably-correct static auth template referenced elsewhere in this project's own history (`deterministic_patcher.py`) — this is extending existing infrastructure, not building new. Directly targets why `todo`'s `crud_ok` has never once been `True` in 16 canary runs. **Highest-confidence, lowest-cost, highest-volume item on this list.**

2. **Deterministic edit-endpoint (PUT/PATCH) completeness check**, same generation-time mechanism as #1, targeting the second-largest bundle group (3/14, 21%, `PUT /products/{id}` → 405). Slightly higher cost than #1 since it needs an entity-aware template rather than one fixed file.

3. **Give MissingEndpoint (48 instances, the single largest cluster) a deterministic repair path.** Detection is already solid (2 confirmed static validators); the entire gap is on the repair side, which currently costs an LLM call every single time with no deterministic fallback and no confirmed success-rate evidence.

4. **Investigate why repair attempts 2+ almost never succeed** (100%→3%→0%→25%→0% by fix_count). Not a cluster-specific fix — a structural repair-loop question with enormous potential ROI given how clean the signal is. Possible outcomes: cap the loop earlier (saves cost with no accuracy loss) or find and fix why later attempts regress.

5. **Deterministic seed/test-data FK-consistency check** (the 1/14 bundle where a seeded payload references a nonexistent row, `"Priority ID does not exist"`). Structurally simple, low cost, directly evidenced.

6. **AttributeError long-tail triage** — before adding a 4th patcher, determine (via the same bundle-decomposition method this experiment used for JourneyCRUDFailure) whether the remaining 18 instances cluster into 1-2 dominant sub-shapes or are genuinely diffuse. Mirrors this experiment's own most valuable method, applied to the next-largest unresolved cluster.

7. **Resolve the ValidationError / ResponseValidationError / PydanticSerializationError taxonomy overlap** before investing further repair effort in any of the three — confirm via tracing one real raw error string through `_classify_validation_error()` whether these are 1, 2, or 3 genuinely distinct problems.

8. **Add JWT/Auth as its own distinct taxonomy category**, split out of the generic `JourneyCRUDFailure` bucket. Currently auth-shaped failures are invisible as a trackable trend — this is also the only reliable way to later confirm whether fix #1 actually worked (a category that doesn't exist can't show a before/after).

9. **Wire failure bundles back into `generation_log.jsonl` consistently.** Currently only 1 of 87 log entries references a bundle; 13 of 14 forensic bundles have no corresponding log entry, meaning most of this cycle's richest evidence came from a different, not-fully-integrated code path. A pure data-plumbing fix that makes every future investigation of this kind cheaper and more reliable.

10. **General ImportError static check** — currently zero dedicated detection exists for this 13-instance, runtime-crashing cluster; even a narrow AST-based "does every imported name exist in its target module" check would catch a meaningful fraction before runtime.

11. **General ModuleNotFoundError static check** — same rationale as #10 for this 9-instance cluster; only a narrow `__init__.py`-specific fix currently exists.

12. **Add a static pre-check for PydanticSerializationError** — 4+ repair patchers already exist with no corresponding detection; adding one would reveal whether that repair investment is actually resolving the issue or repeatedly papering over the same undetected gap (`last_seen` is still current despite the investment).

13. **Re-verify whether SyntaxError's most recent (2026-07-11) instance is the already-fixed querystring-as-filename bug regressing, or a genuinely new shape.** Cheap (inspect one fresh generation), high-value either way — confirms a real regression worth re-fixing, or rules one out and closes the question.

14. **Re-verify ConfigAttributeError's apparent one-day-post-fix closure** — same cheap-check logic as #13; resolves whether this project's strongest "solved" candidate is fully real or has a residual edge case.

15. **Clarify whether RouterExportMismatch's detection is genuinely separate from its repair function** (`_patch_router_export_mismatch`) or combined — closes a documentation gap this cycle's own investigation flagged but couldn't resolve in the time available.

16. **Confirm or rule out a Dependency-failure category** — zero evidence found in any source checked this cycle, but only existing logs were checked; a targeted look at the pipeline's own pip/npm install steps would confirm "genuinely doesn't happen" versus "not captured by current telemetry."

17. **Investigate the JourneyCRUDFailure runner-targeting bug** (1/14 bundles hit `/stats/summary` instead of the intended entity-create endpoint) — this is evidence of a bug in ForgeAI's own test harness, not the generated app; fixing it improves telemetry accuracy for every future investigation, though it doesn't directly improve any user-facing app.

18. **Reconcile `strategy_outcomes.json`'s coarse 7-bucket repair-strategy ledger against the 21-pattern taxonomy** — two parallel, not-obviously-linked data models currently track repair success at different granularities; unifying them (or documenting why they're intentionally separate) would sharpen every future ROI analysis like this one.

19. **Formally retire the durably-fixed clusters** (`NoReferencedTableError`, `RelationshipModelNotImported`, both stale 15+ days with zero recurrence) in the taxonomy itself — a `low-priority`/`retired` flag so future dashboards and experiments don't re-surface them as live problems worth new investment.

20. **Add Deployment-stage telemetry** (currently zero data — canaries run with `--no-deploy` by explicit project convention). Lowest priority on this list: this is a known, intentional gap, not an oversight, and this project's own live-deployment reliability was already separately audited (per this project's own prior-experiment history) — listed for completeness against Part 2's example cluster list, not because the evidence suggests it's urgent.
