# Experiment 093 — Live Validation of Ownership Assignment Repair

2026-07-13. Live, two canaries (both allowed by this experiment's own
constraint), $0.1022 total / 170,480 tokens. `backend/scripts/exp093_canary.py`,
wrapping `app.services.deterministic_patcher._patch_missing_ownership_assignment`
to log every invocation where content actually changed — same
non-invasive methodology as Exp079/082/086/089.

## 1. Runs

| Label | Idea | Score | Status | Activations |
|---|---|---|---|---|
| `exp093-validation-r1` | todo (`Task.user_id`) | 92.0/100 (A), deploy-ready | OK (vs. prior baseline) | 0 |
| `exp093-validation-r2` | CRM (`Contact.user_id`/`owner_id` shape) | 89.9/100 (B), deploy-ready | BASELINE | 0 |

Both clean: full CRUD journey pass, 100%/15-15 and 14-14 endpoint pass
rates, no regression detected by the canary's own comparison.

## 2. Activation: 0/2 — but for two genuinely different, both-informative reasons

**Neither run shows `_patch_missing_ownership_assignment` itself firing
live.** Investigated why in each case rather than treating this as a
single undifferentiated "inconclusive":

**Run 1 (todo)**: the initial generation already had
`user_id=current_user.id` correctly present (confirmed by reading the
final file directly, §5) — so there was nothing for the patch to find on
its first pass. But **the exact target bug DID occur live, mid-run**:

```
[fix] REGRESSION: 1 new error(s) with no score improvement (92.0 -> 68.9)
     ↳ [UserIdNotInjectedError] sqlalchemy.exc.IntegrityError: ...
  [fix] Reverted to pre-fix snapshot
```

An unrelated LLM-driven fix attempt (targeting some other diagnostic)
rewrote `task_routes.py` and, as a side effect, dropped the ownership
assignment — reproducing this experiment's exact target defect. It
didn't reach `_patch_missing_ownership_assignment` because the
**existing regression-detection-and-revert mechanism caught and rolled
it back first**, restoring the last-known-good snapshot (which already
had the assignment). The score track confirms this happened **twice** in
one run: `92 → 92 → 69 → 92 → 69 → 92`.

This is a materially useful, non-null result: it's direct, live
confirmation that the underlying failure mode this experiment targets is
still a real, currently-occurring risk in the pipeline (an LLM rewrite
can still drop ownership assignment), and that today it's caught by a
*different*, already-existing safety net (regression-revert) before
`_patch_missing_ownership_assignment` gets a turn. Since deterministic
patches (including this one) re-run after every fix attempt as part of
`run_deterministic_patches()`, this new patcher is a **second,
independent layer** that would catch the same regression if it were ever
allowed to persist past a revert (e.g. if the regression detector's own
heuristics happened to miss a case, or the buggy attempt happened to
score marginally higher despite the defect) — not redundant with the
existing safety net, complementary to it.

**Run 2 (CRM)**: no regression cycle at all — the generation was clean
throughout. Confirmed directly by reading the final generated file (§5):
`Contact(**{...}, user_id=current_user.id)`, correct from the start.
Nothing for the patch to do, honestly reported as zero activations
rather than a false claim of "detection failed."

## 3. Verify (Task 3) — using what each run actually exercised

- **Injected exactly once**: not exercised live this cycle (0
  activations) — already covered by Exp092's own offline replay (12/12
  tests including an explicit idempotency check, plus the full-corpus
  scan finding exactly one injection per genuinely-broken file).
- **Models without ownership FK remain untouched**: not directly
  re-exercised here (no such model appeared in either run's route
  files needing this specific check) — already covered offline
  (Exp092 §5/6, `inventory_manager`'s `Product`/`Transaction`).
- **Existing ownership logic preserved**: directly confirmed in both
  live runs — `todo_list_app`'s and `simple_crm`'s constructor-kwarg
  assignments (`user_id=current_user.id`) were never touched or
  duplicated by the patch, consistent with the preservation logic
  verified offline in Exp092.

## 4. Runtime validation (Task 4/5)

- No `NOT NULL`/ownership `IntegrityError` in either run's **final**
  state (the one live occurrence, in run 1, was caught and reverted
  before reaching the final report).
- Create endpoints: both final states show `Create entity: 201` in the
  CRUD journey.
- CRUD journey: PASS in both final states (11/11 steps, todo; CRM's
  journey also PASS both checkpoints in its log).
- No ownership regressions: canary comparison reports `OK`/`BASELINE`
  for both, no regression flagged.

## 5. Comparison against Exp091 replay expectations (Task 6)

Both final generated files match the exact correct shape Exp091's own
investigation described as the "fixed" pattern (`todo_list_app`'s
`Task(**{...}, user_id=current_user.id)`,
`simple_crm`'s `Contact(**{...}, user_id=current_user.id)`) —
consistent with what a correctly-functioning pipeline should produce,
whether via the LLM getting it right unaided or via this patch
correcting it. No divergence from expectations found.

## 6. Observatory update (Task 7)

- **Activation count**: 0/2 canaries this cycle (see §2 for the
  non-trivial reasons why, not a blanket "didn't work").
- **Ownership repairs**: 0 live this cycle; 2 confirmed via Exp092's
  own offline full-corpus replay (`lean_sales_crm`, `support_ticket_system`)
  remain the primary evidence of real-world correctness.
- **Runtime result**: PASS/PASS, 92.0/100 (A) and 89.9/100 (B), both
  deploy-ready, no regressions.
- **Remaining JourneyCRUDFailure prevalence**: not re-measured this
  cycle (would require a fresh `generation_log.jsonl` scan across many
  more generations than 2 canaries provide) — the live-observed
  regression-then-revert episode in run 1 is itself one additional data
  point supporting Exp090/091's original prevalence estimate (the
  underlying defect is still generatable, just currently caught by a
  different mechanism before persisting to a tracked failure).

No permanent dashboard counter added — same reasoning as every prior
cycle in this series: no accumulated real activation data yet exists to
justify one.

## 7. Recommendation for Exp094

Two reasonable next steps, not mutually exclusive:

1. **Keep this thread closed for now.** Two clean canaries (no
   regression, correct ownership in both final states) plus Exp092's own
   thorough offline replay (2 genuine real-project hits, 12/12 tests)
   is a reasonable evidence bar, matching this project's own established
   precedent (Exp082/086/089 all closed similarly after 1-2 inconclusive
   live runs backed by strong offline evidence). Further live attempts
   have the same "might not reproduce the exact pattern this generation"
   problem every prior live-validation cycle in this series has hit.
2. **Worth a dedicated look, not urgent**: the live-observed
   regression-then-revert episode (run 1) suggests re-examining whether
   the regression-detection-and-revert mechanism's own coverage is
   complete, or whether there are cases where a similar ownership-
   dropping regression could slip through without a score-based signal
   catching it (e.g. if the LLM's rewrite happened to also silently fix
   an unrelated issue, netting a flat or slightly positive score despite
   losing ownership assignment). Not investigated this cycle — flagged
   as a candidate, not a confirmed gap.

Given the broader post-stabilization framing from Exp090, **recommend
returning to the taxonomy**: re-scan `generation_log.jsonl`/`patterns.json`
for the current highest-impact remaining active class (likely the
Edit-path "405/no entity_id" JourneyCRUDFailure sub-shape Exp091
explicitly scoped out, or a fresh scan given time has passed) rather than
continuing to spend Cerebras budget chasing live confirmation of an
already offline-verified, twice-cleanly-canaried fix.

**Deliverables**: this doc, `experiments.md` entry,
`backend/scripts/exp093_canary.py`,
`backend/benchmark_results/exp093_ownership_assignment_invocations.json`,
two canary history entries (`exp093-validation-r1` OK 92.0,
`exp093-validation-r2` BASELINE 89.9). **Cost: $0.1022, two live
generations.**
