# Experiment 080 — Investigate Retry Strategy Memory Staleness

2026-07-12. Investigation only, $0, zero Cerebras calls. Per this
experiment's own constraints: no implementation, evidence built entirely
from `git log`/`git show` on already-committed telemetry plus direct code
reading.

## 1. Root cause

**`regenerate_module`'s 0-success blacklist entries for `contract`, `api`,
`SyntaxError` (and probably `AttributeError`) are frozen using evidence
that is at minimum 6 days stale, and predates at least two confirmed,
material fixes to `_regenerate_module`'s own implementation — including
Exp078's.** `strategy_memory.should_skip()` has no time-awareness,
version-awareness, or code-awareness of any kind: it's a pure
all-time tally, so once a (pattern, strategy) pair crosses the 3-tries/
0-successes threshold, the retry manager skips it forever, which means the
tally can never grow, which means it can never un-cross the threshold —
a self-reinforcing permanent lock with no mechanism to reflect that the
underlying code changed.

## 2. Evidence chain

**Storage format** (`backend/failure_memory/strategy_outcomes.json` via
`app/retry/strategy_memory.py`, read directly): `{pattern_id: {strategy:
{"tries": N, "successes": N}}}`. Rewritten in full on every
`record_outcome()` call (`json.dumps(..., sort_keys=True)`), **no
timestamps, no version field, no per-try history — only a monotonic
lifetime counter.**

**Git history of the file itself** (it's tracked, "human-readable,
git-trackable" per its own module docstring) gives the only available
timeline, since the data format carries none:

| Commit | Date | `contract/regenerate_module` | `api/regenerate_module` | `SyntaxError/regenerate_module` | `contract/patch_file` (control) |
|---|---|---|---|---|---|
| `02acf4f` (earliest tracked) | 2026-07-06 | 0/3 | 0/3 | 0/2 | 6/22 |
| `fd5f8cd` | 2026-07-10 | 0/3 | 0/3 | 0/2 | 20/84 |
| `6552639` | 2026-07-11 | 0/3 | 0/3 | 0/2 | 27/97 |
| `aeb3fd8` (Exp078 commit) | 2026-07-12 | 0/3 | 0/3 | 0/2 | 48/124 |
| `HEAD` (post-Exp079) | 2026-07-12 | 0/3 (unchanged) | 0/3 (unchanged) | 0/2 (unchanged) | 50/126 |

`regenerate_module`'s counts for these three patterns are **byte-identical
across every tracked snapshot spanning 6 days**, while the control
column (`patch_file` for the same `contract` pattern) grew by over 100
tries in that same window — i.e., dozens of real repair-loop runs
happened against the dominant `contract` failure pattern in that time,
and not one of them was ever allowed to try `regenerate_module` again once
it first crossed the threshold. This is direct, mechanical confirmation of
the self-reinforcing lock, not an inference: the *tries* count is frozen
because `should_skip()` prevents the strategy from ever running, which is
exactly the failure mode this experiment set out to check for.

(`AttributeError`'s `regenerate_module` entry first appears in the
`aeb3fd8` snapshot rather than every prior one — but `aeb3fd8` was a
single commit that swept in ~230 pending changes accumulated across
several prior *uncommitted* sessions [see Exp078's own commit message],
so this only proves the entry existed in the working tree by 07-12, not
when it actually originated. Treating it as "possibly also stale, exact
onset unconfirmed" rather than claiming the same 6-day proof the other
three patterns have.)

**Confirmed material implementation changes to `_regenerate_module` since
the failures were recorded** (`git log --follow` on `orchestrator.py`,
diffs read directly):

- `ef9eebc` (2026-07-11, *"Reject syntactically invalid Python before
  writing a repair-loop fix to disk"*) — added a syntax-validation gate
  inside `_regenerate_module`'s write loop. Before this, a malformed/
  truncated LLM response could be written straight to disk and then fail
  for a reason having nothing to do with whether the regeneration
  strategy itself was sound.
- `aeb3fd8` (2026-07-12, Exp078) — wired `required_endpoints=` into
  `generate_architecture_fix()`, the fix this whole investigation chain
  traces back to.

Since the frozen counts were already at their current values by
`02acf4f` (2026-07-06) — i.e., before either of these fixes — **every
recorded `regenerate_module` failure for these patterns happened under an
implementation that no longer exists.**

## 3. Statistical validity of the historical outcomes

Not valid as evidence about the *current* implementation, for two
independent reasons:

1. **Confounded**: the treatment (what "trying `regenerate_module`" meant)
   materially changed twice since the data was collected. A 0/3 record
   under the old implementation says nothing about the new one — this is
   the same logic as distrusting an A/B test result after the "B" variant
   was rewritten.
2. **Small-n**: even setting aside staleness, 2–3 tries per pattern is a
   thin sample to declare anything "proven ineffective" — `should_skip()`'s
   own `min_tries=3` threshold is already a low bar, and this experiment's
   findings don't suggest raising it (that's a separate, unrelated
   question); the finding here is specifically that whatever bar is
   chosen, it should apply to evidence gathered under the *current* code.

By contrast, `contract/patch_file` (50 successes / 126 tries) and
`contract/regenerate_arch` (9/42) remain large, continuously-refreshed,
untouched-by-any-relevant-code-change samples — **this investigation
found no reason to distrust those**, consistent with this experiment's
constraint to preserve retry behavior wherever historical evidence
remains valid. Only `regenerate_module`'s entries for these specific
patterns are implicated.

## 4. Correction mechanisms considered

| Option | Verdict |
|---|---|
| **Versioned strategy identity** (bump a name/key, e.g. `regenerate_module_v2`) | Precise, but requires remembering to rename at every call site and rewrites keys across the whole store — more invasive than needed. |
| **Implementation hash** (hash `_regenerate_module`'s source, store per entry) | Fully automatic, but opaque (a hash mismatch explains nothing to a human) and over-sensitive — a comment or formatting change would invalidate history for no functional reason, and scoping the hash to "only the semantically relevant lines" is itself a judgment call, not a mechanical one. |
| **Experiment generation** (tag entries with the experiment number active when recorded; bump a small per-strategy version int when a numbered experiment materially changes that strategy) | **Smallest fit**: this project already numbers and logs every reliability change as an "Experiment N" in `experiments.md` — a discipline already in place, not a new one to adopt. Adding one integer field per stored entry (`"version": N`) plus one small in-code table (`_STRATEGY_IMPL_VERSION = {"regenerate_module": 2, ...}`, bumped exactly when a change like Exp078 lands) lets `should_skip()`/`record_outcome()` ignore entries recorded under an older version — precise (per-strategy, not global — `patch_file`'s untouched history is never affected by a `regenerate_module` version bump), deterministic, and human-auditable (the number in the code literally corresponds to an experiment doc). |
| **Bounded lookback** (only count the most recent K tries) | Simple and generic, but the current storage is a monotonic counter with no per-try history to bound — would itself require a storage-format change, and even then doesn't specifically know a fix happened; it just eventually forgets, on an arbitrary schedule unrelated to when the code actually changed. |
| **Timestamp expiry** (store a timestamp, expire records >N days old) | Requires the same schema addition as the generation-tag option but ties invalidation to wall-clock time rather than actual causality — a strategy untouched for 3 months would wrongly regain trust with zero new evidence, while a strategy fixed yesterday wouldn't get to reset until its window expires. |
| **Manual reset** (one-time edit/clear of the 4 confirmed-stale entries) | Correctly unblocks *today's* known case with zero new mechanism, but doesn't prevent the identical staleness problem next time any strategy's implementation changes — not a lasting fix, only a patch for the current symptom. |

## 5. Recommended correction

**Experiment-generation tagging**, as the standing mechanism, **plus** a
one-time manual reset of the 4 entries this investigation specifically
proved stale (`contract`, `api`, `SyntaxError`, and — with the caveat
above — `AttributeError`, all under `regenerate_module`) as the immediate
unblock, since generation-tagging only prevents *future* staleness and
doesn't retroactively fix data already frozen under the old, tagless
format.

Concretely (for Exp081 to implement, not this cycle):

1. Add `"version": N` to each stored `{tries, successes}` entry.
2. Add a small module-level table in `strategy_memory.py` mapping
   strategy name → current implementation version, bumped by whoever
   lands a reliability experiment that materially changes that strategy's
   code (the same moment they'd write the `experiments.md` entry anyway).
3. `record_outcome()` stamps new entries with the strategy's current
   version; `should_skip()` only aggregates entries whose stored version
   matches the strategy's current version (older-version entries are
   invisible to the skip check, not deleted — kept for audit/history).
4. One-time: set `regenerate_module`'s version to reflect Exp078 (and
   `ef9eebc`) having landed, which — under the new logic — immediately and
   correctly stops counting the 4 pre-existing frozen entries without a
   separate manual JSON edit.

## 6. Estimated impact on runtime reliability

`contract` is, by a wide margin, the highest-volume failure pattern in the
whole system (126 recorded `patch_file` tries, 42 `regenerate_arch` tries
— both larger than every other pattern combined). It is also the exact
pattern Exp077's confirmed-live incident traced through, and the exact
pattern Exp079's canary hit. Unblocking `regenerate_module` for this
pattern means:

- Exp078's endpoint-preservation fix can finally be evaluated against
  real, live `contract`-pattern failures instead of remaining permanently
  untested in production.
- The fix loop regains a real middle rung between `patch_file` (attempts
  1–2, sometimes insufficient for multi-line drift) and `regenerate_arch`
  (the nuclear, most expensive option — 42 tries recorded, itself only
  9/42 = 21% successful) for the system's most common failure category —
  a plausible efficiency and cost improvement independent of the
  endpoint-preservation question specifically, since it's the difference
  between a full-module rewrite and a full-architecture redesign.

Impact is a plausible, well-motivated hypothesis at this point, not yet a
measured number — that measurement is exactly what a follow-up live
validation (after Exp081 lands) would produce.

## 7. Recommendation for Exp081

Implement the generation-tag mechanism described in §5, scoped to
`app/retry/strategy_memory.py` only (no changes to `RetryManager`'s
public interface, no changes to `_regenerate_module` itself — this
experiment's own constraint to preserve retry behavior wherever historical
evidence remains valid stays satisfied automatically, since
`patch_file`/`regenerate_arch`'s untouched histories are never affected).
Offline-test against a reconstructed version of today's exact frozen
`strategy_outcomes.json` snapshot before any live validation. Only after
that lands would a further live-validation cycle for endpoint preservation
specifically be worth Cerebras spend on the `contract` pattern.

**Deliverables**: this doc, `experiments.md` entry. No code changes, no
Cerebras calls, per this cycle's own constraints. **Cost: $0.**
