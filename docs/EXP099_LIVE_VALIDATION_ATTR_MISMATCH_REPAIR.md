# Experiment 099 — Live Validation of Cross-Type Attribute Access Repair

2026-07-13. Live, two Cerebras canaries (both allowed by this
experiment's own constraint). `backend/scripts/exp099_canary.py` wraps
`app.services.deterministic_patcher._patch_attr_access_mismatches` to
diff every route file it touches during a real generation, non-invasive
— same methodology as Exp079/082/086/089/093/096.

## 1. Runs (Task 1)

| Label | Idea | Score | Outcome |
|---|---|---|---|
| `exp099-validation-r1` | todo (Exp097's exact confirmed architecture/incident shape) | 76.9/100 (C), NEEDS REPAIR | ran 4/5 fix attempts, ended degraded (see §5) |
| `exp099-validation-r2` | auth-heavy (registration/verification/reset, `benchmarks/golden/14_auth.txt`) | 0.0/100 (F) | **failed at architect JSON-parsing stage**, 3 retries, before any backend code was generated — unrelated, pre-existing infrastructure fragility, **$0 cost** (retries hit the LLM response cache) |

r2 is uninformative for this experiment's validation goal (no backend
code, no route files, nothing for `_patch_attr_access_mismatches` to
touch) but did not consume budget, so the two-canary allowance is
effectively still available if a future cycle wants a genuine
auth-heavy data point.

## 2. Attribute-access repair activation (Task 2)

`_patch_attr_access_mismatches()` activated **twice** in r1 (both
identical — the same file re-verified across two of the run's repair
passes, not two different fixes), zero times in r2 (never reached
backend generation).

r1's activation: `user_routes.py`, `user.display_name = user_in.display_name`
→ `user.username = user_in.display_name`.

## 3. Verification (Task 3)

**Confirmed via isolated fixture testing, not just the live diff, that
this specific activation is PRE-EXISTING behavior — not new to
Exp098.** Reconstructed the exact shape (`Users` model with no
`display_name` column, `UserUpdate` schema with one, `Users =
_get_user_model()` indirection) and ran it through both the current
patcher and the exact pre-Exp098 commit (`acd1f46~1`, via a controlled
git-checkout swap, restored immediately after): **identical output,
byte-for-byte, on both versions.** `user` was already resolvable as a
`Users` instance via the pre-existing `_query_target_class`/ORM-query
typing path (unrelated to Exp098's new `schema_cols`), and
`"display_name"` was already a pre-existing `_FIELD_SYNONYMS_PATCHER`
key. **This run therefore provides zero direct live evidence of the
NEW Pydantic-schema pathway (`schema_cols`) firing** — the one thing
that did fire was already covered by the old code.

- **SQLAlchemy repairs still work**: confirmed (the activation above,
  reproduced identically on old and new code).
- **Pydantic repairs execute when appropriate**: not directly
  demonstrated by either live run this cycle (r1's one activation
  turned out to be SQLAlchemy-only; r2 never reached backend
  generation). Substitute evidence: Exp098's own offline full-corpus
  replay already found and independently verified 2 genuine
  Pydantic-schema-driven fixes against real, previously-generated code
  (`personal_expense_tracker`'s `RegisterRequest.username` → `.email`,
  `support_ticket_system`'s `AuthRegisterRequest.full_name` →
  `.username`) — both confirmed correct against the actual schema
  source at the time. Treating that as the operative evidence for this
  specific sub-question, since this cycle's live runs didn't happen to
  need it.
- **No incorrect synonym substitutions from Exp098's own additions**:
  confirmed — the only activation observed uses a pre-existing key
  (`display_name`), not the new `"name"`/`"password"` curated keys.

**A real, but PRE-EXISTING correctness gap was found via this live
run's own generated code**, not caused by Exp098: `update_user`'s
`if user_in.display_name is not None: user.username = user_in.display_name`
(post-fix) sits in a separate `if` block from the correct
`if user_in.username: user.username = user_in.username` a few lines
above (`app/routes/user_routes.py:76-87`, `todo_list_app`). A client
PUT-ing only `display_name` (not `username`) would silently overwrite
the user's actual login username with their display-name value — worse
than the pre-fix behavior (a silent no-op, since the model never had a
`display_name` column to write to at all). This is `_patch_attr_access_mismatches`'
general design characteristic of rewriting assignment TARGETS the same
as READS, confirmed identical on both pre- and post-Exp098 code — not
something this experiment introduced, and out of scope for a fix here
per the "no implementation unless a NEW deterministic issue" constraint
(this one is pre-existing). Flagged prominently for Exp100 (§6).

## 4. Runtime validation (Task 4/5)

**Historical AttributeError class absent**: confirmed — the final
`generation_log.jsonl` record for this run shows
`['[SQLAlchemyError] sqlalchemy.exc.StatementError: ...']`, no
`[AttributeError]` tag anywhere in this run's telemetry. Neither of
Exp097's two confirmed shapes (`User.name`, `UserCreate.password`)
recurred.

**Registration**: succeeded (`200 @ register` in every journey pass).
**Seed script**: failed on the FIRST pass with `TypeError: 'display_name'
is an invalid keyword argument for Users` (`seed_routes.py:70`,
`db.add(Users(**udata))`) — **this is a different bug shape than
Exp097/098 targeted**: a dict-unpacked `**kwargs` constructor call, not
a `.attr` read/write `_patch_attr_access_mismatches` operates on (it
only walks `ast.Attribute` nodes, never `ast.keyword`/dict-unpack
arguments). New finding, flagged for Exp100 (§6), not fixed this cycle.
**CRUD journey**: passed 10/11 on the first two verification passes,
but **degraded to 6/11 by the run's final state** — `Create entity`
started 500-ing, cascading into 4 more failed steps (`no entity_id
captured`). Traced this to an **unrelated, later fix-loop attempt**:
the final `generation_log.jsonl` tag (`SQLAlchemyError` /
`StatementError` / SQLite data-type `TypeError`) appears only after
two intermediate LLM-driven fix groups targeting `PydanticSerializationError`
and various contract violations (`TaskCreate.title`, duplicate
`UserCreate` class) — none of which involve `_patch_attr_access_mismatches`
at all. **Not caused by Exp098** (confirmed: the patcher fired exactly
once, on an unrelated file, with output identical to pre-Exp098 code).

## 5. Comparison against Exp098 replay expectations (Task 6)

Matches expectations for the SQLAlchemy path exactly (§3). Does not
provide a live comparison point for the Pydantic path (no live
Pydantic-schema activation this cycle) — offline corpus evidence
stands in, per §3.

## 6. Observatory update (Task 7)

- **Activation count**: 2 (both identical, r1 only; pre-existing
  behavior, not new to Exp098).
- **Repaired AttributeErrors**: 0 live-observed this cycle (none
  occurred to repair) — consistent with Exp097's 2 confirmed incidents
  both being historical, and this run generating a structurally similar
  but different bug (constructor-kwarg TypeError, not attribute-access
  AttributeError).
- **Runtime outcome**: r1 NEEDS REPAIR (76.9/C) at final state, journey
  degraded from 10/11 to 6/11 over the repair loop — root-caused to an
  unrelated `SQLAlchemyError` from a later fix attempt, not Exp098. r2
  failed at architect stage (unrelated), $0 cost.
- **Remaining taxonomy**: `canary_health` is now `Unhealthy` (driven by
  r2's 0.0 architect-stage failure being the most recent canary entry —
  an unrelated infrastructure issue, not an attribute-access repair
  regression). `top_failure_now` still shows `AttributeError` in the
  Observatory's rolling window (reflecting Exp097's already-known
  historical incidents, not a new one from this run — confirmed via
  direct `generation_log.jsonl` inspection, this run's own tag is
  `SQLAlchemyError`). Two new, distinct issues surfaced this cycle,
  neither an `AttributeError`:
  1. `Users(**udata)` constructor-kwarg dict-unpack mismatches
     (`TypeError`, not `AttributeError`) — a sibling gap
     `_patch_attr_access_mismatches` doesn't cover by design (it only
     targets `.attr` access, never `**kwargs` unpacking).
  2. The pre-existing identity-field assignment-target clobbering issue
     (§3), now confirmed with a concrete, live-generated repro.

## 7. Recommendation for Exp100

Two candidate investigations surfaced by this cycle, both genuinely new
findings (though the second predates Exp098 itself):

1. **`Users(**udata)` constructor-kwarg field mismatches** — a
   `TypeError`, not `AttributeError`, so outside Exp097/098's own
   taxonomy scope, but the same underlying "seed_routes.py guesses
   field names independently of the real model" root cause (Exp097
   §3-4) manifesting via a different AST shape. Worth checking whether
   `patch_filter_dict_unpack_constructor_kwargs` (an existing patcher
   visible in `prevention_counts`) already targets this and simply
   didn't fire, or whether it's a genuine coverage gap.
2. **Assignment-target rewriting risk for the identity-field cluster**
   (`username`/`display_name`/`full_name`) — confirmed pre-existing,
   confirmed live-reproducible, real data-corruption risk (not just a
   missed-fix no-op). Consider restricting `_patch_attr_access_mismatches`
   to READ (Load) contexts only for this specific cluster, or requiring
   that a rewritten assignment target not already have a separate,
   correct assignment to the same attribute elsewhere in the same
   function.

Do not re-run a live canary purely to force a Pydantic-pathway
activation — Exp098's offline corpus evidence (2 independently-verified
real fixes) is sufficient, and this cycle's null result for that
specific sub-question is uninformative but not concerning, consistent
with this series' established pattern (Exp093, Exp096).

**Deliverables**: this doc, `experiments.md` entry,
`backend/scripts/exp099_canary.py`,
`backend/benchmark_results/exp099_attr_mismatch_invocations.json`,
regenerated `backend/observatory_report.html`, two canary history
entries (`exp099-validation-r1` BASELINE 76.9, `exp099-validation-r2`
BASELINE 0.0 — both `BASELINE` rather than OK/REGRESSION because the
script's regression check only compares against the immediately-
preceding canary_history run, which didn't have matching `todo`/
`auth_heavy` app-key entries to diff against).
**Cost: $0.0546, one live generation (r2 cost $0, failed before any
billable backend generation).**
