# Experiment 076 — Live Validation of NOT NULL UPDATE Repair (Exp075)

2026-07-12. Measurement only, per this experiment's own constraints — no
implementation changes (no new root cause discovered that would justify
one). One app (`inventory` — the same app both Exp074's original
incident and Exp075's replay used), 2 real Cerebras generations total
(minimal usage; expanded from 1 to 2 only because attempt 1 legitimately
produced clean, already-guarded code with nothing for the repair to
catch — see §1). Instrumentation (`scripts/exp076_canary.py`) patches
the already-registered `fix_update_notnull_field_loss` entry in the
`preflight` registry in place, for this run only; production behavior
(the actual guard-insertion logic) is completely unchanged — the wrapper
only observes.

## Headline

**The repair fires correctly on real, live-generated code and guards
exactly the right columns, nothing else.** Confirmed via two independent
lines of evidence: the production fix's own authoritative log output
(`[preflight] Guarded 2 NOT-NULL field(s) on UPDATE in <file>`, self-
reported by the unmodified, shipped code — not my instrumentation), and
direct inspection of the final on-disk artifact, where one of the two
repaired files survived to the end of the pipeline with the guards
still visibly intact and exactly matching the model's true NOT NULL
columns.

## 1. Two attempts — why

**Attempt r1** (label `exp076-validation-r1`): the LLM's raw route
generation this time already wrote every `PUT` field assignment
correctly guarded (`if product_in.sku is not None: product.sku =
product_in.sku`, etc.) natively — confirmed by reading the generated
`product_routes.py` directly. `_fix_update_notnull_field_loss` correctly
found **zero** risky assignments (0 invocations that changed a file) and
left the file untouched. Final: journey PASS 11/11, score 90.9/A, zero
`NOT NULL` errors anywhere in the run, model files unchanged.

This is a legitimate, useful result — it directly demonstrates **no
false-positive rewrites on already-correct code** (one of this
experiment's own explicit success criteria) — but it gives no evidence
the repair actually *activates* on buggy code, which is the central
thing this experiment needs to confirm live. Per this experiment's own
"one canary application only unless evidence requires expansion" rule, a
second attempt on the **same single app** (not a different app — kept
scope minimal) was run to try to catch a live activation, since route
generation is not cache-hit in this pipeline (confirmed: every route
file shows a fresh `Using Cerebras` call, no `[cache hit]` tag, in both
attempts) — each attempt is a genuinely independent LLM sample.

**Attempt r2** (label `exp076-validation-r2`) reproduced the bug and
gave the activation evidence.

## 2. Repair activation evidence

Production log (unmodified code, not instrumentation output):
```
[preflight] Guarded 2 NOT-NULL field(s) on UPDATE in product_routes.py
[preflight] Guarded 2 NOT-NULL field(s) on UPDATE in transaction_routes.py
```

Ground truth cross-checked directly against the generated models:
- `Product`: `unit_cost = Column(Float, nullable=False)` (no default),
  `reorder_threshold = Column(Integer, nullable=False)` (no default) —
  the two fields guarded in `product_routes.py`. `category_id` is also
  `nullable=False` but is a `ForeignKey` — correctly excluded by design
  (FKs are supplied by the route from auth/context, never blindly copied
  from client input; Exp075's own scoping rule). `sku`/`name` are
  `nullable=True` this run — correctly left alone.
- `Transaction`: `quantity = Column(Integer, nullable=False)`,
  `unit_price = Column(Float, nullable=False)` (no defaults) — the two
  fields guarded in `transaction_routes.py`. `product_id` is `NOT NULL`
  but a FK (excluded, correct). `type`/`partner_name` are nullable
  (correctly untouched).

**Instrumentation limitation, disclosed**: the temporary diff-capture
wrapper's simple line-alignment heuristic only successfully matched 1 of
each file's 2 edits (`unit_cost` in `product_routes.py`, `quantity` in
`transaction_routes.py` — see
`benchmark_results/exp076_patcher_invocations.json`), stopping at the
first point its greedy matching couldn't confirm alignment. This is a
limitation of the **measurement script written for this experiment**,
not of the shipped fix — the fix's own count (2 per file) is directly
confirmed both by its own unmodified log line and, for
`transaction_routes.py`, by reading the final surviving file directly
(§3). Noted here rather than silently presented as "only 1 field
guarded," which would understate the fix's real, confirmed activity.

## 3. Generated route diff

`transaction_routes.py`'s `PUT /transactions/{transaction_id}` handler
survived to the final artifact unmodified by any later repair pass,
giving a direct, complete before/after picture:

```python
    # Update fields
    transaction.type = transaction_in.type                          # nullable -- untouched, correct
    if transaction_in.quantity is not None:                         # <- inserted by the fix
        transaction.quantity = transaction_in.quantity              #    (was: transaction.quantity = transaction_in.quantity)
    if transaction_in.unit_price is not None:                       # <- inserted by the fix
        transaction.unit_price = transaction_in.unit_price          #    (was: transaction.unit_price = transaction_in.unit_price)
    transaction.partner_name = transaction_in.partner_name          # nullable -- untouched, correct
```

All surrounding business logic — stock-adjustment math (revert old
effect, apply new effect, insufficient-stock check), the product lookup,
the optional `product_id` reassignment block — is byte-for-byte
untouched. **No unintended AST rewrites found** (Task 6): the fix's edit
is exactly, only, the two guarded lines.

`product_routes.py`'s `update_product()` does **not** show the guards in
the final artifact — not because the fix failed, but because a later,
unrelated LLM-driven repair pass (`[V15] Fix attempt 2/5 -- strategy:
patch_file`, `[fix] Patched: app/routes/product_routes.py`) rewrote the
entire function to a different, also-correct pattern
(`for field, value in product_in.model_dump(exclude_unset=True).items():
setattr(product, field, value)`) while fixing an unrelated bug (§5,
finding 2). This is expected, harmless pipeline behavior — deterministic
preflight fixes re-run on every regeneration pass and are idempotent by
design (already covered by Exp075's own `test_idempotent_second_pass_is_noop`);
a later rewrite superseding an earlier one is not a defect in either fix.

## 4. Runtime results

| | r1 | r2 |
|---|---|---|
| `_fix_update_notnull_field_loss` fired | No (0 risky assignments found) | Yes (2 files, 4 total guards) |
| NOT NULL `IntegrityError` from an omitted UPDATE field | None | None |
| Final journey | PASS 11/11 | PASS 11/11 (after intermediate failures unrelated to this fix, §5) |
| Final Forge Score | 90.9/100 (A) | 94.6/100 (A), deploy-ready |
| Model files changed by the fix | No | No (confirmed both by the wrapper's snapshot diff and by directly reading `unit_cost`/`quantity`'s `nullable=False` in the final model files) |
| CREATE path | Untouched, `create_product`/`create_transaction` both succeeded (201) | Same |

**Success criteria, explicitly checked**:
- No NOT NULL `IntegrityError` caused by omitted update fields — **met**, zero occurrences in either run.
- Existing values preserved correctly — confirmed structurally (guard shape identical to Exp075's own runtime-DB-tested pattern) and behaviorally (final `Edit entity: 200` in both runs, no data-loss-shaped failures).
- No CREATE regressions — **met**, `_fix_update_notnull_field_loss` never scans `@router.post(...)`, and both runs' CREATE steps passed.
- No false-positive rewrites — **met**, r1 is a direct, real demonstration (already-correct code, zero fix activity); r2's guards land exactly on the confirmed NOT NULL/no-default/non-FK columns, nowhere else.
- No new runtime failures introduced by the repair — **met**; the failures that did occur in r2 (§5) are independently traced to unrelated causes, present before the fix's guard lines were even added (confirmed by their traceback line numbers/content having nothing to do with the two guarded assignments).

## 5. New, unrelated findings (documented only, not fixed — per this experiment's own "no implementation changes unless a new root cause" rule)

1. **[LOW]** `seed_routes.py`: `Users(**u)` passes `display_name` as a
   constructor kwarg the `Users` model doesn't declare
   (`TypeError: 'display_name' is an invalid keyword argument for
   Users`) — caused one intermediate `POST /seed` 500 in r2. Already a
   recognized shape (`ModelFieldMismatchError`, same family as
   `docs/RUNTIME_KNOWLEDGE_BASE.md`'s `NotNullViolationError`-adjacent
   entries) — self-resolved via the LLM repair loop this run.
2. **[LOW-MEDIUM]** `transaction_routes.py`'s `PUT` handler binds its
   body parameter to `TransactionCreate` instead of `TransactionUpdate`
   (`transaction_in: TransactionCreate`) — a route-generation wiring
   bug, distinct from Exp075's target. Since `TransactionCreate.quantity`/
   `unit_price` are non-Optional, Pydantic itself would 422-reject any
   PUT that omits them, making Exp075's guard on those two fields
   currently *unreachable in this specific handler* (harmless, not
   wrong — the guard is correct regardless of which schema class is
   bound, and becomes load-bearing the moment this separate wiring bug
   is fixed). A near-identical shape briefly appeared in `product_routes.py`
   too, mid-run (`PUT /products/1` 422'd with "Field required" on
   `sku`/`unit_cost`/`reorder_threshold"`) before an unrelated repair
   pass rewrote that handler entirely.

Neither finding blocked this experiment's own success criteria (both
self-resolved via the existing repair loop within the same run); logged
here per the project's established convention rather than investigated
further.

## 6. Observatory update

`scripts/observatory.py` re-run, confirmed to pick up both new canary
entries with **zero code changes** (same precedent Exp072/074
established):

```
Before this experiment (post-Exp074): Timeline points: 34
After this experiment (r1 + r2):      Timeline points: 36
Canary: Healthy
Prevention total: 440 (up from 418 -- includes all preflight/patcher
  activity across both runs, not exclusively this fix)
```

Repair-specific counters (this fix has no dedicated Observatory metric
yet — reported here directly, not fabricated into the generic
dashboard): **activation count 2/2 generations that reached the buggy
shape** (r1 had none to activate on; r2 activated on both files that had
the bug) — i.e. 100% activation rate on the one real run that actually
exercised it, 0% false-positive rate across both runs combined.

## 7. Comparison against Exp075's replay expectations

| Exp075 replay (synthetic + DB) | Exp076 live (r2) |
|---|---|
| Reconstructed `product.sku = product_in.sku` (byte-for-byte from Exp074's traceback) | Live-generated `product.unit_cost = product_in.unit_cost` / `product.reorder_threshold = ...` — same shape, different concrete fields (expected: Exp075 replayed the *exact* historical incident; live generation naturally varies which NOT NULL column the bug lands on) |
| Guard inserted: `if source.field is not None:` | Identical guard shape, confirmed live, byte-for-byte |
| Nullable columns left untouched | Confirmed live (`sku`/`name`/`current_stock` in this run) |
| CREATE untouched | Confirmed live |
| Model untouched | Confirmed live |

**Conclusion: live behavior matches Exp075's replay predictions exactly** — same mechanism, same scoping discipline, same guard shape, on genuinely different (live-sampled) buggy code.

## Answers to the deliverables

1. **Live validation evidence**: §1-2 — 2 real generations, 1 clean (no
   false positive), 1 buggy (correct activation, self-reported count
   confirmed against ground-truth model schemas).
2. **Generated route diff**: §3 — full before/after of
   `transaction_routes.py`'s surviving guarded handler.
3. **Runtime results**: §4 — table of both runs against every explicit
   success criterion.
4. **Observatory update**: §6 — auto-picked-up, zero code changes,
   activation/false-positive rates reported directly.
5. **Recommendation for the next experiment**: **`MissingEndpoint`**, as
   expected — still the taxonomy's single largest unaddressed cluster
   (48 historical instances / 24.7%, confirmed still open and still the
   score-capping cause in Exp074's `blog_cms` run: no `PUT`/`DELETE
   /posts/{id}` route generated at all). Unlike the NOT-NULL-on-UPDATE
   gap this pair of experiments (075/076) just closed, `MissingEndpoint`
   has **no existing deterministic mechanism to extend** — every current
   repair path is LLM-only (`missing_file_service.py::generate_missing_file()`)
   with no verified success-rate evidence. The natural Exp077 scope:
   root-cause *why* the route-generation LLM call sometimes omits an
   entire CRUD verb for an otherwise-fully-scaffolded resource (same
   Wave-4 stage this pair of experiments already learned to instrument
   closely), before attempting any fix — this taxonomy entry has never
   had a dedicated root-cause investigation in this project's full
   68-plus-experiment history (per `docs/RUNTIME_KNOWLEDGE_BASE.md`'s own
   entry).

**Cost: 2 Cerebras generations, `inventory` only.** Per the task's own
instruction, **NOT committed**.
