# ForgeAI V16 RC1 — Ranked Bottleneck Proposal

**Date**: 2026-07-07
**Status**: Analysis only — nothing in this document has been implemented.
**Sources consulted**: `backend/failure_memory/generation_log.jsonl` (49 records,
telemetry restored by Experiment 003), `backend/failure_memory/patterns.json`
(376 total runs since 2026-06-21 — the full-history pattern tracker, not
just this session's canaries), `experiments.md` (Experiments 001-020),
`docs/adr/ADR-001-*.md`, `docs/adr/ADR-002-*.md`,
`docs/V16_STABILIZATION_REPORT.md`, and Graphify (`graphify query` against
the route-generation/frontend-API call path).

---

## Top 5 Remaining Bottlenecks, Ranked

Ranked by frequency (from `patterns.json`'s 376-run corpus), severity,
engineering effort, expected reliability improvement, and expected
reduction in repair-loop spend.

| Rank | Bottleneck | Frequency (count, last_seen) | Severity | Est. Effort | Expected Improvement | Expected Repair-Loop Reduction |
|---|---|---|---|---|---|---|
| **1** | **Endpoint & route contract drift** (`MissingEndpoint` + `RouterExportMismatch`) | **48** (39 + 9; `MissingEndpoint` last_seen 2026-07-06, recurred again in today's own Experiment 020 canary — blog_cms) | High — blocks Runtime, CRUD, Browser, and deploy-readiness simultaneously | **S-M** — detection is already 100% built; the gap is only in the fix path | High | High — each occurrence today costs a non-deterministic LLM repair call |
| 2 | `ConfigAttributeError` | 11 (last_seen 2026-07-06T20:29 — the exact crash that confounded Experiment 017's crm run) | Medium-High — crashes generation before reaching CRUD at all | S | Medium | Medium |
| 3 | Frontend build/import errors (`FrontendBuildError` 7, `ModuleNotFoundError` 9, `ImportError` 11) | 27 combined, but `FrontendBuildError` hasn't recurred since 2026-06-30 (Experiment 014's scaffolder likely already suppressed most of it) | Medium | S-M | Medium | Low-Medium (already partially mitigated) |
| 4 | Relationship/secondary-table extraction gap | `SQLAlchemyError` 11, `NoReferencedTableError` 6, `RelationshipModelNotImported` 3 (20 combined; explicitly flagged as the natural next extension in *both* ADR-001 and ADR-002) | Medium | M | Medium-High (improves schemas, routes, AND seeds at once) | Medium |
| 5 | `JourneyCRUDFailure` (generic bucket) | 29 | — | — | — | **Not independently rankable** — this is a downstream symptom bucket, not a root cause. A large fraction of its historical mass was already absorbed by ADR-001 (schema drift) and ADR-002 (unseeded lookup tables); what remains likely maps onto items 1-4 above once traced, not a fifth independent cause. |

**Why endpoint/route contract drift ranks #1**: it is the single largest
pattern in the entire 376-run corpus (39 occurrences alone, more than
double the next-largest specific pattern), it recurred in *this session's
own* Experiment 020 canary (blog_cms's `GET /articles?author_id=`,
`GET /authors/${userId}`, `POST /articles` calls with no matching backend
route — exactly this pattern, exactly the regression that confounded
ADR-002's own aggregate score), and — critically for effort estimation —
**the detection machinery already exists and is already deterministic**.
This is the same shape of finding that produced both ADR-001 and ADR-002:
not "we need to build a detector," but "a detector already exists and is
either inert or feeding a non-deterministic fix."

---

## Deep Dive: Endpoint & Route Contract Reliability

### 1. Root-Cause Analysis

There are, right now, **two independent, overlapping detection
mechanisms** for this exact failure class — neither of which drives a
deterministic fix:

**(a) `backend/app/services/endpoint_validator.py`** (imperative,
currently live and load-bearing — this is what produces the
`MissingEndpoint` pattern text seen in `patterns.json`):
- `extract_actual_backend_routes(project_path)` — parses the real
  generated route files for their actually-declared `@router.get/post/...`
  paths. This is, functionally, already "`entity_metadata.py` for
  routes" — a deterministic extractor of ground truth from generated code,
  built independently of ADR-001's extractor but doing the same kind of
  job for a different artifact type.
- `validate_endpoints(...)` — compares the architect's planned endpoint
  list against what `extract_actual_backend_routes` found; emits
  `"Missing endpoint {method} {path} (expected in {file})"` for anything
  planned but never implemented.
- `validate_frontend_api_calls(project_path, errors)` — the frontier
  most directly matching this milestone's own framing ("Routes → Frontend
  → API → Browser"): checks that a frontend API call actually resolves to
  something the backend implements.
- `validate_orphan_routes(...)` — the reverse direction (a route file
  exists but isn't registered anywhere).

**(b) `backend/app/contract/validator.py`'s `ContractConformanceValidator`**
(declarative, AppContract-based, confirmed via direct code read to be
**inert in practice**): `_check_api_calls_reference_endpoints()` implements
the *identical* check — "every frontend `api_call` references a declared
endpoint" — but its own module docstring states plainly: *"Checks 2-4 are
currently INERT in practice: the adapter... leaves
`contract.frontend.api_calls`... empty, because the architecture stage
doesn't produce that data yet."* Confirmed directly in
`app/contract/adapter.py:176` — `ContractFrontend()` is constructed with no
`api_calls` argument, so it's always `[]`, so the check's loop body never
executes, so it can never emit a single finding, ever, regardless of how
badly a generated app's frontend and backend disagree.

**The actual gap, once both mechanisms are accounted for**: detection is
solid on the `endpoint_validator.py` side (confirmed live, confirmed
producing the exact `MissingEndpoint`/frontend-api-call findings in
`patterns.json`). What's missing is a **deterministic fix**. Today, every
one of these 48+ findings routes to the same generic path every other
missing-file case used to (before ADR-002): a fresh, non-deterministic LLM
call (`generate_fix`/`generate_missing_file` in `v6_orchestrator.py`) that
may or may not produce a correct route, and costs tokens every time it
runs. There is already **one precedent for a deterministic fix in this
exact family** — `_patch_wire_orphan_routers()` (referenced in
`v6_orchestrator.py`, handles the case where a route *file* exists but its
router was never imported into `main.py`) — proving the "detect
deterministically, fix deterministically" pattern already works for one
sub-case of this problem. The remaining sub-case (a frontend call
referencing a path/verb that simply doesn't exist anywhere in the backend,
or exists under a different name — e.g. blog_cms's frontend calling
`/articles` against a backend that only implements `post_routes.py`'s
`/posts`) has no deterministic fix path yet.

### 2. Smallest Deterministic Solution

Following the exact "generate once, reuse everywhere" principle from
ADR-001/ADR-002:

Treat `extract_actual_backend_routes()`'s output (the real, generated
route list — already deterministically extracted, already proven correct
since it's been running in production since before this session) as
**ground truth**, the same way ADR-001 treats the generated model as
ground truth for schema generation. When `validate_frontend_api_calls`
detects a frontend call that doesn't match any real backend route:

1. First, attempt a **deterministic resource-alias match**: if the
   backend has a route for the same HTTP verb whose path differs only by
   a resource-name variant already knowable from the architecture plan
   (e.g. `/articles` vs `/posts` — both map to the same entity in the
   architect's plan), rewrite the frontend call to the real path. This
   mirrors ADR-002's FK-target/table-name resolution logic conceptually
   (`_singular`/`_plural`-style normalization, already proven in
   `entity_metadata.py`), applied to endpoint paths instead of table
   names.
2. If no deterministic alias resolves (a genuinely missing endpoint, not
   a naming variant), fall back to today's existing non-deterministic LLM
   repair — **never worse than current behavior**, exactly the same
   fallback discipline ADR-002 used (static stub as the floor, never the
   ceiling).
3. Wire the (currently inert) `ContractConformanceValidator` for free, as
   a side effect: since step 1 requires extracting the real frontend
   `api_calls` from generated source anyway (to know what to rewrite), the
   same extraction can populate `AppContract.frontend.api_calls`, which
   instantly activates `_check_api_calls_reference_endpoints()` — a
   second, independent validator gets real data for the first time, at
   near-zero marginal cost, since the extraction work is shared.

This does **not** require a new IR, a new architect prompt, or an
architecture-wide rewrite — the same scope discipline ADR-001 used
explicitly ("deliberately narrower than AppContract").

### 3. Alternative Solutions Considered

- **Full AppContract enforcement** (make the architect natively emit
  `ContractEndpoint`/`ContractApiCall` and enforce conformance as a hard
  gate): this is the VNext report's original, larger proposal. Rejected
  for the same reason ADR-001 rejected it for model/schema drift — L-effort,
  touches the architect prompt and every generator, and the narrower fix
  above already reuses 90% of the value at a fraction of the cost.
- **Just raise `ContractConformanceValidator`'s findings from LOW to a
  blocking severity, without fixing the data-population gap**: does
  nothing — the check would still never fire, since `api_calls` stays
  empty regardless of severity.
- **Add a brand-new extractor duplicate of `extract_actual_backend_routes`
  inside `entity_metadata.py` or a new module**: rejected per the
  "single source of truth" lesson from ADR-002's own Task 2 review finding
  (a duplicated helper was caught and removed) — reuse the existing,
  already-live `endpoint_validator.py` extractor instead of building a
  second one.

### 4. Expected Benchmark Impact

Direct: elimination or reduction of the single largest failure pattern in
the 376-run corpus (39 `MissingEndpoint` + a chunk of `RouterExportMismatch`
and generic `JourneyCRUDFailure` instances that trace to this root cause,
per Experiment 020's own blog_cms finding this session). Indirect:
frees repair-loop budget currently spent on non-deterministic endpoint
regeneration for other failure classes. Following this project's own
established discipline (ADR-001, ADR-002): judge success by whether the
specific failure signature (`MissingEndpoint`, frontend-call-to-nonexistent-
endpoint) measurably decreases in `patterns.json` on a post-fix canary, not
by aggregate Forge Score alone — aggregate score is provably confounded by
independent LLM-generation variance, as this session's own ADR-002 canaries
demonstrated twice.

### 5. Estimated Implementation Effort

**S-M** (comparable to ADR-002, likely smaller): the two deterministic
extractors already exist (`extract_actual_backend_routes`,
`entity_metadata.py`'s resource-name normalization helpers `_singular`/
`_plural`, already proven and directly reusable). New work is: (a) the
alias-matching/rewrite logic itself, (b) wiring its output into
`AppContract.frontend.api_calls` for the side-effect activation of the
existing inert validator, (c) the fallback boundary (mirroring ADR-002's
`generate()` single-try/except pattern), (d) tests. Estimate: 5-7 tasks,
similar TDD/SDD structure to ADR-002.

### 6. Risks

- **Alias-matching could mask a genuine architecture mismatch** if it's
  too aggressive (e.g. rewriting a frontend call to a wrong but
  superficially-similar backend route). Mitigate with a conservative match
  threshold (same entity per the architecture plan, same HTTP verb, and
  a normalized-name match only — not fuzzy/similarity-based matching) and
  the same "never worse than today" fallback discipline.
- **Populating `AppContract.frontend.api_calls` might surface a wave of
  new (currently-invisible) findings from the previously-inert validator**,
  which is desirable but should be validated as LOW-severity/observational
  first (per the validator's own existing S14 risk-mitigation doctrine),
  not immediately promoted to a blocking gate.
- Two parallel detectors (imperative `endpoint_validator.py` + declarative
  `ContractConformanceValidator`) checking overlapping ground risks
  duplicate-finding noise in diagnostics; worth deciding whether to
  eventually retire one in favor of the other, though not blocking for
  this fix.

### 7. New ADR or Existing Architecture?

**Warrants a new ADR (ADR-003 candidate)**, for the same reason ADR-001
and ADR-002 each got one: it's a genuinely new deterministic mechanism
(route-ground-truth-based alias resolution), not a bug fix inside an
existing mechanism, and it establishes a third instance of the same
"generate once, reuse everywhere" principle worth recording as precedent
(routes now join models→schema and models→seed as a third artifact class
resolved this way). Per the project's own frozen-spec-before-implementation
discipline (used for both prior ADRs), this should go through
brainstorming → frozen spec → architecture review → SDD implementation →
multi-line-of-evidence validation before being accepted, exactly as
ADR-002 was — not implemented directly from this proposal.

---

## Recommendation

Endpoint & Route Contract Reliability (this session's own Milestone 1,
and the top-ranked item by hard telemetry) is the evidence-backed next
target. Do not begin implementation from this document — per your
instruction, this is a proposal for review only.
