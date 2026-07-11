# Reliability Opportunity Report

**Date:** 2026-07-11
**Cost:** $0 — no LLM calls, no generations. Existing telemetry (`generation_log.jsonl`,
`patterns.json`, `canary_history.json`) plus fresh corpus sweeps against the 53
already-generated apps in `generated_projects/`.

**Purpose:** Before writing another deterministic validator, answer "what % of
failures would this actually eliminate?" instead of "what else can we
validate?" This report ranks every known remaining failure class by
Prevalence × Severity × Ease-of-deterministic-fix, so the next fix is chosen
by evidence, not by momentum from the last one.

**Policy going forward (adopted this cycle):** no deterministic validator is
written until (1) corpus prevalence is measured, (2) root cause is isolated in
real generated output, (3) existing validators/patchers are checked first so
we don't duplicate coverage, and (4) prevalence × severity gives an estimated
ROI that beats the next candidate on the list.

---

## Data sources and their honest limits

| Source | What it covers | Known limitation |
|---|---|---|
| `patterns.json` (178 recorded instances, 2026-06-22 → 2026-07-10) | All-time crash/error taxonomy across every generation attempt | Cumulative — includes failures already fixed by patchers shipped mid-window (e.g. RouterExportMismatch). Not a live rate. |
| `generation_log.jsonl` (68 entries since the 2026-07-06 telemetry fix) | Recent real generation attempts, `dominant_errors` per run | Small N; mixes canary re-runs of the same 3 apps with occasional one-off generations. |
| `canary_history.json` (23 runs, todo/blog_cms/crm only) | Stage-level pass rates (build/runtime/crud/browser/deploy) | **Stale for this report's purpose** — the last canary run (`m3-relationship-dedupe-confirm`) predates every fix shipped this cycle (role-aware validation, response-schema inheritance, FK drift, model-column fallback). The dashboard's "CRUD journey 13.3%" figure is diluted by all 23 historical runs, not just current state. Treat it as a *pre-fix* baseline, not today's number. |
| Fresh corpus sweep (this report, 53 apps) | Static structural checks: constructor-kwarg drift, relationship-target drift, duplicate model classes | Catches structural/silent bugs that never crash (so never appear in the crash-based sources above) — e.g. the FK drift bug from Experiment 046 returns HTTP 200 with an empty list, not an error, so it would never show up in `patterns.json`. Only checked what it was built to check; not exhaustive. |

**Important asymmetry:** crash-based telemetry (`patterns.json`,
`generation_log.jsonl`) systematically **undercounts silent bugs** — wrong
results that don't throw. Structural corpus sweeps are the only way to find
those. Both source types are needed; neither alone gives the true picture.

---

## Ranked failure classes

| # | Failure class | Prevalence | Source | Severity | Deterministic? | Priority |
|---|---|---|---|---|---|---|
| 1 | MissingEndpoint | 46/178 = 25.8% (all-time) | telemetry | High | Partial — 3 sub-diseases per ADR-003, only `RouterExportMismatch` cleanly deterministic (shipped) | ⭐⭐⭐☆☆ — already investigated, remaining 2 sub-diseases need more telemetry before a design (per ADR-003) |
| 2 | JourneyCRUDFailure (crashing subset) | 29/178 = 16.3% (all-time); canary CRUD-stage pass rate 13.3% | telemetry + canary | High | Partial — role-aware validation (this cycle) improves *detection*, not every underlying app bug | ⭐⭐⭐⭐☆ — but **stale**, see "Immediate recommendation" below |
| 3 | Relationship / model-integrity drift (NEW, this sweep) | 4/53 = 7.5% apps (sports_league_manager, support_ticket_system, volunteer_management_system, gym_tracker) | corpus sweep | **High** — mapper-configuration crash kills the entire backend at startup, forge score ≈ 0 | **Yes** — same technique as Experiment 046 (parse `relationship()` targets + `back_populates` counterparts + duplicate class names, verify against real model classes) | ⭐⭐⭐⭐⭐ — small population, catastrophic per-instance cost, cleanly deterministic |
| 4 | Import-family errors (ImportError + ModuleNotFoundError) | 21/178 = 11.8% (all-time) | telemetry | Medium-High | Yes, largely already shipped (2026-07-03 Import Resolution Fixes) | ⭐⭐☆☆☆ — believed mostly addressed; needs one confirming canary before further investment, not a new validator |
| 5 | ConfigAttributeError | 13/178 = 7.3% (all-time) | telemetry | Medium | Yes, already shipped (config preflight patcher) | ⭐☆☆☆☆ — believed fixed; document, don't rebuild |
| 6 | SQLAlchemyError (general) | 11/178 = 6.2% (all-time) | telemetry | Medium-High | Overlaps significantly with #3 above | Fold into #3's fix, don't treat separately |
| 7 | RouterExportMismatch | 9/178 = 5.1% (all-time) | telemetry | Medium | Yes — shipped (ADR-003) | Done — historical only, expect near-0 going forward |
| 8 | AttributeError (general) | 8/178 = 4.5% (all-time) | telemetry | Medium | Mixed — many are covered by `_patch_attr_access_mismatches`, some are the same drift family as #3 | ⭐⭐☆☆☆ — re-measure after #3 ships, some of this count will disappear |
| 9 | FrontendBuildError | 7/178 = 3.9% (all-time) | telemetry | Medium | Partial — import-path patcher exists, doesn't cover every case | ⭐⭐☆☆☆ |
| 10 | NoReferencedTableError | 6/178 = 3.4% (all-time) | telemetry | High | Likely the same root cause as #3 (FK/relationship targets referencing nonexistent tables) | Fold into #3 |
| 11 | Constructor-kwarg ownership drift (NEW, this sweep) | 1/53 = 1.9% apps | corpus sweep | Medium | Yes, but not worth it alone | **Document only — do not build a validator for this.** Below threshold. |
| 12 | SyntaxError | 5/178 = 2.8% (all-time) | telemetry | High per-instance | Yes — shipped (AST syntax gate in repair loop, Experiment 042) | Done — expect near-0 going forward |
| 13 | Deployment failures | 0/51 "success" | variance report | Unknown | Unmeasured — canary runs `--no-deploy` by default, so this 0% mostly reflects "never attempted," not "failed" | **Not a real signal as currently measured** — don't act on this number |

---

## The two things worth building next, in order

### 1. Relationship / model-integrity drift patcher (⭐⭐⭐⭐⭐)

**Evidence (real, corpus-confirmed, not estimated):**

- `sports_league_manager`: `Match.home_team`/`away_team` both do
  `relationship("Team", ...)` but the actual model class is `class Teams(Base)`
  (plural) — SQLAlchemy's mapper configuration does a string lookup across all
  registered classes and finds none named `Team`. Same pattern for
  `League`/`Leagues`. This is a **hard crash at app-startup mapper
  configuration**, not a per-request error — the whole backend fails to boot.
- `support_ticket_system` and `sports_league_manager` **both** define
  `class User` in `user.py` AND `class Users` in `users.py` — two separate
  model classes shadowing the same real entity. `Tickets.priority` also
  targets `relationship("Priority")` when the real class is `Priorities`.
- `support_ticket_system`'s `AuditLog.user` and `volunteer_management_system`'s
  `Report.user` both declare `back_populates="audit_logs"` /
  `back_populates="reports"` where the target `User` model has **no
  relationships declared at all** — same class of mapper-configuration crash.

**Why this ranks above JourneyCRUDFailure despite lower raw prevalence:**
severity is categorically different. JourneyCRUDFailure usually means one
broken workflow step in an otherwise-running app (partial credit, forge score
in the 40s-70s range). This class means the backend never starts — forge
score ≈ 0, same blast radius as a build-time crash. Prevalence (7.5% of apps)
× severity (total failure) beats JourneyCRUDFailure's higher prevalence ×
partial severity.

**Why it's cleanly deterministic:** identical technique to Experiment 046 —
parse every model's `relationship()` calls and class names (already have
`_CLASS_DECL_RE`, singular/plural tolerance logic reused three times this
session), verify every relationship target class actually exists (with the
same alias-tolerant matching `_patch_model_aliases` already does), verify
every `back_populates` counterpart attribute exists on the target class, and
flag/merge duplicate singular-plural model definitions the same way
`_dedupe_class_files` already partially does for other file types.

**Estimated scope:** likely 150-250 LOC (comparable to Experiment 046),
reusing existing helpers rather than new infrastructure.

### 2. Constructor-kwarg ownership drift — document, don't build

**Evidence:** exactly 1/53 apps (`support_ticket_system`,
`TicketMessages(..., user_id=...)` when the real FK is `.author_id`). Same
underlying bug family as Experiment 046, but at the constructor call site
instead of the query-filter site.

**Decision: do not build a dedicated patcher.** At 1.9% prevalence this fails
the ROI bar on its own. If the Relationship/model-integrity patcher above
ends up needing a shared "resolve real ownership FK for entity X" helper
anyway, extending it to also cover constructor kwargs would be near-zero
marginal cost — revisit *as a follow-on*, not a standalone experiment.

---

## Explicitly deferred, with reason

| Failure class | Why deferred |
|---|---|
| Import-family errors | Believed largely fixed by 2026-07-03 work; needs a confirming canary before any new investment, not more code |
| ConfigAttributeError | Believed fixed by config preflight patcher; same — confirm, don't rebuild |
| Response Drift Audit (Model→Schema→Route→Frontend) | User's own next-in-line item, but per this report's policy it needs its own prevalence sweep before scoping — not assumed high-value just because it's next on a list |
| Deployment reliability | Current 0% figure is a measurement artifact (`--no-deploy` default), not a real failure rate — needs a differently-scoped measurement, not a fix |

---

## Immediate recommendation

**Before building anything new, and before running any canary:** the
CRUD-journey and MissingEndpoint numbers above are the best data we have, but
they predate 8 shipped fixes this cycle (role-aware validation, response-schema
inheritance, model-column fallback, ownership-FK drift, and others). We are
currently flying blind on whether first-try success has actually moved. That's
not a reason to run a full benchmark — it's a reason that **the next single
canary run (3 apps, `--no-deploy`) is now justified**, specifically to refresh
this report's baseline before deciding how much further investment
Relationship/model-integrity drift deserves relative to re-confirming what's
already shipped.

**Sequencing:**
1. Build the Relationship/model-integrity drift patcher (evidence-justified, ⭐⭐⭐⭐⭐, $0).
2. Corpus-sweep it for false positives the same way Experiment 046 was validated, before trusting it.
3. *Then* spend the one canary run — it will simultaneously confirm this fix and refresh every stale number in this report.
4. Re-run this report's telemetry section after that canary lands; re-rank from there rather than assuming the current order still holds.
