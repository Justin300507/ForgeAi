# ADR-003 Investigation: Deterministic Endpoint Contract Repair

**Status**: Investigation only — no code written, no implementation started.
**Date**: 2026-07-07
**Files read in full for this investigation**: `backend/app/services/endpoint_validator.py`,
`backend/app/contract/validator.py`, `backend/app/contract/adapter.py`,
`backend/app/contract/models.py`, the relevant sections of
`backend/app/services/v6_orchestrator.py` (error-attribution and fix-dispatch
logic, lines ~300-340 and ~940-1050) and `backend/app/verification/engine.py`
(`_run_contract_conformance_check`), plus `backend/failure_memory/patterns.json`'s
stored examples for `MissingEndpoint` and `RouterExportMismatch`.

**Headline finding, ahead of the detailed answers below**: my earlier
proposal (`docs/V16_RC1_BOTTLENECK_PROPOSAL.md`) was directionally right but
**more optimistic than the evidence actually supports once read in full**.
The 39 `MissingEndpoint` occurrences are not one uniform bug — they're at
least three different underlying causes with very different fix
difficulty, and only a minority look cleanly deterministic on the evidence
available. `RouterExportMismatch` (9 occurrences) is the one clean,
high-confidence deterministic-fix candidate found this round. This
investigation recommends a **smaller, two-tier ADR-003 scope** than the
prior proposal implied, not the full "eliminate the #1 pattern" framing.

---

## Q1: Exactly which endpoint mismatches are deterministic?

Confirmed deterministic (mechanical, no reasoning about business logic
required):

- **Router export/registration mismatch** (`RouterExportMismatch`, 9
  occurrences — e.g. `"Router export mismatch in
  app/routes/auth_user_routes.py. Expected 'auth_user_router'"`). The route
  file exists and (presumably) implements the right endpoints; the
  *variable name* main.py's import expects doesn't match what the file
  actually exports. This is a pure rename/re-export — the fix doesn't need
  to know anything about what the endpoint does, only that a name needs to
  match. **This is the one item in this investigation with unambiguous,
  high-confidence deterministic-fix potential.**
- **Orphan route file not imported into `main.py`** — already solved
  deterministically today, via `_patch_wire_orphan_routers()`
  (`v6_orchestrator.py:426-427, 536, 995-996, 1038`). Not a gap; cited here
  as the existing proof that this class of problem is tractable
  deterministically, and as the direct precedent this investigation builds on.
- **Orphan frontend page not wired into routing** — same, already solved
  via `_patch_wire_orphan_frontend_routes()` (`v6_orchestrator.py:527, 543,
  1023, 1039`).

Partially deterministic, conditional on a design decision (see Q2):

- **Path/verb convention mismatch on the SAME entity** (e.g. a query
  param vs. path segment, or a plural/singular convention difference the
  architecture plan itself would resolve) — deterministic *if and only if*
  both sides can be proven to reference the same architecture-plan entity.

## Q2: Which currently require LLM reasoning?

Reading `patterns.json`'s stored `MissingEndpoint` examples directly
(the only 5 currently retained in the rolling window) surfaces two
distinct failure shapes that are **not** simple aliasing, and — per the
project's own "evidence first" discipline — I'm reporting this honestly
rather than assuming the optimistic case:

1. **`Missing endpoint WS /ws/issues/{issue_id}/stream`** — a probable
   **validator bug, not a real missing endpoint**. `extract_actual_backend_routes()`
   (`endpoint_validator.py:64-88`) walks every `FunctionDef`'s decorators
   and uppercases whatever method name it finds
   (`decorator.func.attr.upper()`) with no allowlist — so a WebSocket
   route declared as `@router.websocket("/issues/{id}/stream")` would be
   recorded as method `"WEBSOCKET"`, while the architecture plan (and this
   error message) call it `"WS"`. Those two strings never match, so a
   *correctly implemented* WebSocket route would still be reported
   missing. This needs zero LLM reasoning to fix — but it's a validator
   bug fix, not an "endpoint repair," and it should be fixed **first and
   separately**, since it may be silently inflating the entire
   `MissingEndpoint` count with false positives.
2. **`DELETE /classes/{id}`, `GET /classes`, `POST /classes`,
   `POST /assignments/{id}/submissions`** (all four for the same
   generation run's `classes`/`assignments` resources) — every HTTP verb
   for an entire resource missing at once looks like "the whole route
   file was never fully generated," structurally the same shape of
   problem ADR-002 solved for `seed_routes.py`, **except there is no
   deterministic ground truth to derive full CRUD business logic from**.
   ADR-001 and ADR-002 both work because there's an existing artifact
   (the model) to mechanically derive the next artifact from. There is no
   equivalent "model" for arbitrary route-handler business logic — this
   case genuinely needs LLM generation, same as today.
3. **This session's own blog_cms case** (Experiment 020): frontend calling
   `/articles`, `/authors/{userId}` against a backend that only
   implements `/posts` — this is not a superficial spelling difference,
   it's the frontend and backend **independently choosing different
   entity/resource names for the same feature** (an `Article`/`Author`
   concept on one side, `Post`/`current_user` on the other). Naive
   alias-matching risks silently "fixing" this by rewriting one side to
   match the other's WRONG assumption, which is worse than leaving it for
   an LLM to reconcile with actual judgment about which name the
   architecture plan intended.

**Revised estimate**: the deterministically-fixable share of raw
`MissingEndpoint` occurrences is likely a **minority**, not the "single
largest pattern eliminated" framing my first pass implied — bounded above
by whatever fraction turns out to be validator false-positives (case 1)
or genuine plan-consistent aliases (a narrower version of case 3), and
excluding whole-file generation gaps (case 2) entirely, since those need
real logic, not a rename.

## Q3: Can `endpoint_validator.py` produce enough structured information to repair deterministic cases automatically?

**Yes, today, for `RouterExportMismatch`** — its own error message
(`"Router export mismatch in {file}. Expected '{name}'"`) already contains
the file and the exact expected variable name; producing a fix is a
mechanical AST rename, no new extraction needed.

**Not yet, for `MissingEndpoint`** — the validator currently reports
`(method, path, expected_file)` but not *why* it's missing (validator bug
vs. whole-file gap vs. alias vs. genuine plan disagreement). Building that
classification is real, non-trivial design work — more than "reuse the
existing extractor," which is why this investigation recommends
instrumenting the classification *before* committing to a repair engine's
scope (see Recommendation, below).

## Q4: Can `ContractConformanceValidator` be revived instead of replaced?

**Partially, and it needs a second fix first.** Two findings, both
confirmed by direct code read:

- It is **already live** in the V15 pipeline — `verification/engine.py`'s
  `_run_contract_conformance_check` (Stage 2a2) calls it on every run,
  warn-only/LOW-severity by design. The `app/contract/models.py` docstring
  claiming *"Nothing in the live pipeline imports this module yet"* is
  **stale** — that was true when the module was first written, not now.
- However, `AppContract.endpoints` (populated by
  `from_architecture_plan()`, `adapter.py:153-160`) is derived purely from
  the **architecture plan**, not from `extract_actual_backend_routes()`'s
  real, generated-code ground truth. Even after fixing the
  `frontend.api_calls` population gap (the originally-identified cause of
  its inertness), `_check_api_calls_reference_endpoints()` would only
  verify "does the frontend call something the *plan* declared" — not
  "does the frontend call something the *backend actually implements*."
  `endpoint_validator.py` already does the *stronger*, correct check
  (real generated frontend code vs. real generated backend code). Reviving
  the contract validator without also reconciling its `endpoints` list
  against real generated routes would add a weaker, partially-redundant
  second opinion, not new detection value.

**Recommendation on this specific question**: don't prioritize reviving
`ContractConformanceValidator` as the primary mechanism. If pursued at all,
its more valuable role is a *plan-fidelity* check (did a deterministic
repair correctly preserve what the architecture plan intended, rather than
just making two generated artifacts internally consistent with each
other) — a secondary, not primary, use case.

## Q5: Can both validators produce a single `EndpointContract` object consumed by deterministic repair?

Architecturally clean, and I'd recommend it **if** Tier 2 work
(alias-resolution) is pursued — but the object should be built primarily
from `extract_actual_backend_routes()`'s real output (the correct ground
truth, per Q4), with the architecture plan and `AppContract.endpoints`
folded in only as *secondary* signal for resolving ambiguous cases (e.g.
disambiguating which planned entity an oddly-named real route corresponds
to). This mirrors ADR-001's own principle exactly: the *generated
artifact* is ground truth, the *plan/architecture* is advisory context —
not the other way around. Building this is real, non-trivial work,
appropriately scoped as its own task within a larger plan, not a
one-liner.

## Q6: Estimates

Reported with the confidence level the evidence actually supports, not
inflated:

- **Router export mismatches removable**: high confidence, likely
  close to 100% of the 9 occurrences — this is a clean rename operation
  with no ambiguity in the available evidence.
- **`MissingEndpoint` occurrences removable deterministically**: **not
  yet known with confidence** from the 5-example rolling window
  available. A validator-bug fix (WS/method-label case) could reduce the
  *reported* count for free, without "fixing" anything CRUD-relevant.
  Genuine alias-resolvable cases are plausible but unquantified — the
  honest answer per this project's own evidence-discipline is **"needs a
  short instrumentation pass to measure the actual split before committing
  to an effort estimate,"** not a number pulled from a 5-example sample.
- **Repair-loop / API-cost reduction**: proportional to whichever share
  of the above turns out deterministically fixable — expect a clear,
  measurable win on `RouterExportMismatch` alone (9 occurrences × 1 LLM
  call each, historically, now zero), and an unknown-but-plausibly-modest
  additional win on `MissingEndpoint` pending the instrumentation above.
- **Engineering effort**: Tier 1 (router-export rename + WS/method-label
  validator fix) is **S**, comparable in shape to earlier deterministic
  patcher fixes (Experiment 009-style), not a new architectural mechanism.
  Tier 2 (alias-resolution repair engine + reconciled `EndpointContract`)
  is **M**, comparable to ADR-002's actual scope, and should not be
  committed to until Tier 1 ships and the instrumentation pass reports
  real numbers.

---

## Recommendation

**Split ADR-003 into two tiers rather than one combined proposal:**

- **Tier 1 (do this first, high confidence, small scope)**: deterministic
  router-export-mismatch repair (reuse the validator's own error-message
  structure directly) + a fix to `extract_actual_backend_routes()`'s
  method-label handling so non-HTTP-verb decorators (`websocket`, or any
  future non-CRUD decorator) don't produce false `MissingEndpoint`
  reports. Neither requires a new architectural mechanism or a new ADR on
  its own scale — closer in size to the project's existing deterministic
  patcher fixes than to ADR-001/002.
- **Tier 2 (defer, needs more evidence)**: build an `EndpointContract`
  from real generated routes + real generated frontend calls, and a
  conservative alias-resolution repair step for same-entity naming-
  convention differences — but only after Tier 1 ships and a few canary/
  benchmark runs report the real breakdown of remaining `MissingEndpoint`
  causes (validator-bug / whole-file-gap / genuine-alias), since that
  breakdown determines whether Tier 2 is worth building at all, and at
  what scope. This tier, if it proceeds, is the one that would warrant a
  full ADR-003 write-up on the scale of ADR-001/002 — Tier 1 does not.

This is not a rejection of the ADR-003 direction — the underlying
principle ("detect deterministically, repair deterministically, only fall
back to LLM reasoning when the fix genuinely requires it") holds up. It's
a narrower, more evidence-honest first step than "eliminate the #1
pattern," which is what a closer reading of the actual failure examples
supports.
