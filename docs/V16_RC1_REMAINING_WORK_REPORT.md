# ForgeAI V16 RC1 — Remaining Work Report

**Date**: 2026-07-07
**Method**: no new code reads, no new investigation — synthesized from
`experiments.md` (Experiments 001-022), `docs/adr/*`,
`docs/ADR-003-investigation.md`, `docs/V16_STABILIZATION_REPORT.md`,
`docs/V16_RC1_BOTTLENECK_PROPOSAL.md`, and the freshest
`backend/failure_memory/patterns.json` snapshot (385 total runs,
last_updated 2026-07-07T12:32:33). One-off patterns (count=1:
`TimestampNotNullError`, `MonolithicSchemaError`, `FastAPIError`,
`RelationshipMissingError`, `UserIdNotInjectedError`,
`InvalidDependsType`, `ModelFieldMismatchError`) are excluded per
instruction — noise, not classes.
**Purpose**: this is the roadmap, not a to-do list to execute
immediately. No fix should start from this document without a fresh,
today's-telemetry check first — several of these classes have already
had partial or full fixes land *during this same session*, and raw
historical counts can overstate what's still actually broken.

---

## Recurring Failure Classes, Ranked

| # | Class | Combined Count | Last Seen | Existing Fix Coverage | Deterministic Solution Exists? | Est. ROI |
|---|---|---|---|---|---|---|
| 1 | Endpoint/route contract drift | 53 (`MissingEndpoint` 44 + `RouterExportMismatch` 9) | 2026-07-07 | `RouterExportMismatch` fully fixed (Exp 021); `MissingEndpoint` untouched | Partial — investigated in depth (ADR-003), found heterogeneous | Medium — high raw count, but ROI per additional hour of work is now uncertain (see below) |
| 2 | `JourneyCRUDFailure` (generic bucket) | 29 | 2026-07-06 | N/A — not a root cause, a symptom aggregate | N/A | Not independently actionable |
| 3 | `ConfigAttributeError` | 13 | 2026-07-07 (today, this session) | Two fixes shipped this session (Exp 009 historically, pydantic-class extension today) — **third fix not yet canary-confirmed** | Yes, and largely built | High, pending confirmation |
| 4 | Relationship/FK extraction gap | 20 (`SQLAlchemyError` 11 + `NoReferencedTableError` 6 + `RelationshipModelNotImported` 3) | 2026-07-06 | None — explicitly out of scope in both ADR-001 and ADR-002 | Yes, well-specified (extend `entity_metadata.py`) | Medium-High, but M-effort, not started |
| 5 | Frontend/dependency reliability | 27 (`ImportError` 11 + `ModuleNotFoundError` 9 + `FrontendBuildError` 7) | mostly 2026-06-30/07-01 (stale) | Partial (Exp 014's import scaffolder); today's own investigation (Exp 022) found a `ModuleNotFoundError` instance was transient/self-corrected by the repair loop before final scoring | Unclear — needs a persistence check first | Low-Medium, uncertain until re-measured |
| 6 | `SyntaxError` | 5 | 2026-07-06 | Likely already resolved (query-string-filename bug, fixed commit 4af31b4, confirmed by re-canary) | N/A if resolved | Low — verify it's actually gone before touching |
| 7 | Schema/serialization edge cases | 13 (`PydanticSerializationError` 4 + `NotNullViolationError` 4 + `ValidationError` 3 + `ResponseValidationError` 2) | 2026-07-06 | Partially covered by ADR-001 (model-driven schema) and the NOT-NULL requiredness refinement (Exp 012/013); residual is likely edge cases those didn't reach | Uncertain without classifying the residual | Low-Medium |

---

## Detail Per Class

### 1. Endpoint/route contract drift

**Root cause**: per the full `ADR-003-investigation.md`, this is **not
one bug** — `RouterExportMismatch` (pure rename/alias mismatch) was
cleanly deterministic and is now fixed. `MissingEndpoint` (44, still the
single largest raw count in the corpus, and it grew from 39→44 during
this very session) is a mix of at least three different causes: a
validator false-positive class (confirmed already resolved at the
source, 2026-06-30), whole-route-file generation gaps (no deterministic
ground truth exists for arbitrary business logic, same limitation that
makes ADR-001/002 inapplicable here), and genuine frontend/backend
entity-naming disagreements (risky to auto-resolve).

**Existing fix coverage**: `RouterExportMismatch` — done (Exp 021).
`MissingEndpoint` — none; `EndpointContract`/alias-resolution (Tier 2)
was explicitly deferred pending a cleaner breakdown of remaining causes.

**Whether another deterministic solution exists**: partially — the
easy sub-case is shipped. The harder sub-case's deterministic
tractability is genuinely unknown without the classification pass ADR-003
recommended (whole-file-gap vs. alias vs. still-undiscovered causes) — a
diagnostic/instrumentation task, not a fix, is the correct next step here
if this class is picked up again.

**Estimated ROI**: the raw count is the largest in the corpus, but per
this session's own repeated finding (large-count categories often
decompose into several unrelated, smaller-yield problems on closer
inspection — true for both this class and, partially, for Config), don't
treat the count alone as the ROI signal.

### 2. `JourneyCRUDFailure` (generic bucket)

Not a root cause — a downstream symptom aggregate. A meaningful fraction
of its historical mass was already absorbed by ADR-001 (schema drift) and
ADR-002 (unseeded lookup tables); more of it likely overlaps with classes
3-7 below. Not independently actionable; expect it to keep shrinking as
the underlying classes are addressed, and don't invest in it directly.

### 3. `ConfigAttributeError`

**Root cause**: multiple, independently-discovered compounding bugs in
one preflight patcher (`_fix_config_missing_attrs`) across two sessions:
instance-vs-class scoping and case-sensitivity (Experiment 009, fixed
2026-07-06), and — found and fixed *today*, in this same session — a
false assumption that pydantic `BaseSettings`/`BaseModel` subclasses
can't be safely patched at the class level (they can; confirmed
empirically: a post-class-body `setattr` is invisible to pydantic's
model-building machinery and resolves via ordinary Python attribute
lookup on any fresh instance).

**Existing fix coverage**: two of what appear to be all the known
sub-cases are fixed. The count (13) includes at least one occurrence
*from earlier today, before the pydantic fix landed* — so the true
remaining rate can't be read from this snapshot; it needs a fresh canary
specifically to confirm the pydantic fix actually lands the improvement
its local reproduction predicts (the canary that would have shown this
was killed mid-run and produced no data).

**Estimated ROI**: high, contingent entirely on that pending
confirmation — this is the one class in this report where the fix is
already done and just needs its benchmark, not new design work.

### 4. Relationship/FK extraction gap

**Root cause**: `entity_metadata.py` (the ADR-001/002 shared extractor)
only parses `Column(...)` definitions — it does not read
`relationship(...)` calls or secondary/many-to-many tables. This was
flagged as an explicit, deliberate scope boundary in *both* ADR-001 and
ADR-002, not an oversight. `SQLAlchemyError`/`NoReferencedTableError`/
`RelationshipModelNotImported` are the observable consequences.

**Existing fix coverage**: none — genuinely unstarted.

**Whether another deterministic solution exists**: yes, and it's the
most architecturally "shovel-ready" item in this report — extending an
already-proven, already-reused extractor is a smaller lift than either
ADR-001 or ADR-002 was (the extraction pattern and its consumers already
exist; only the relationship-parsing piece is new). This is genuinely a
Milestone-3-shaped ADR-001 extension, not a new architectural decision.

**Estimated ROI**: Medium-High. Improves schemas, routes, and seeds
simultaneously (per ADR-002's own "Future Extensions" note) rather than
one failure mode at a time — the best count-per-effort ratio of any
not-yet-started item here, but it's an M-effort project, not a quick
patch, and shouldn't be started without its own brainstorm/spec pass
(per this project's established discipline for anything beyond a
single-function patch).

### 5. Frontend/dependency reliability

**Root cause**: mixed — missing imports, missing dependencies, build
failures. `FrontendBuildError`'s own last_seen (2026-06-30) predates
Experiment 014's import-scaffolder fix and hasn't recurred since,
suggesting that fix is holding. `ImportError`/`ModuleNotFoundError` are
slightly fresher (2026-07-01) but **today's own Experiment 022 found a
`ModuleNotFoundError` instance that looked like a persisting bug turned
out to be transient** — already self-corrected by the pipeline's own
repair loop before the final scored artifact was produced. That's a
material finding for this whole class: raw pattern counts may include
retries that self-heal, not just persisting defects.

**Whether another deterministic solution exists**: unclear until that
distinction is made for this class specifically — the same
"transient-vs-persisting" check that resolved Experiment 022 should be
applied here before designing anything.

**Estimated ROI**: Low-Medium, and genuinely uncertain — this is the
class most likely to be smaller than its raw count suggests.

### 6. `SyntaxError`

Likely already resolved — this pattern's stored examples are the
query-string-used-as-filename bug (`/tasks?limit=5&...` → invalid module
name), fixed and confirmed via re-canary (memory: `project_querystring_
route_bug`, commit 4af31b4, todo 25.5→76.4). The last_seen date
(2026-07-06) doesn't distinguish pre- from post-fix occurrences from this
snapshot alone. Recommend a quick grep-the-fix-log check before spending
any further effort here, not a code investigation.

### 7. Schema/serialization edge cases

`PydanticSerializationError`, `NotNullViolationError`, `ValidationError`,
`ResponseValidationError` — smaller counts, likely a residual after
ADR-001 (model-driven schema) and the Experiment 012/013 requiredness
refinement already captured the bulk of this class. Worth a
classification pass (which of these are genuinely NEW field-mismatch
shapes vs. edge cases the existing fixes don't reach) before any new
design work — lowest-priority item in this report given the small counts
and existing partial coverage.

---

## Recommendation

Per your instruction, this report is not a to-do list to execute
sequentially tonight. The most defensible next actions, in order of
confidence (not necessarily the order to do them):

1. **Confirm the `ConfigAttributeError` pydantic fix** with one clean
   canary run — this is finished work waiting on its own evidence, the
   highest-confidence item here.
2. **Classify, don't yet fix, remaining `MissingEndpoint` occurrences**
   (whole-file-gap vs. alias vs. other) before any `EndpointContract`
   design work, per ADR-003's own conclusion.
3. **Check whether `ImportError`/`ModuleNotFoundError`/`SyntaxError`
   still persist to final artifacts** (the same transient-check that
   resolved Experiment 022) before assuming any of them need new
   infrastructure.
4. **Relationship/FK extraction** is the one item here that looks like
   genuine, well-justified new architectural work (an ADR-001 extension)
   — but per the "3-5 bugs, one benchmark" workflow change, bundle it
   with whatever else survives steps 1-3 rather than treating it as an
   immediate standalone project.

This matches the phase assessment: Phases 1-3 (generation, deployment,
deterministic-failure removal) are substantially done; the work ahead is
variance reduction — finding failure *classes*, not chasing individual
bugs — and this report is that classification, not a new set of fixes to
start immediately.
