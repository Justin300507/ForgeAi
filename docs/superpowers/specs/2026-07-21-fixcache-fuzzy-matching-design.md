# Experiment 133: Composite-Key Near-Duplicate Matching for FixCache

**Status:** approved, not yet implemented
**Positioning:** a low-risk optimization experiment, not a strategic pillar. Expected to recover a small number of low-risk misses caused by incidental numeric noise, not to meaningfully change the overall repair success rate.

## Motivation

`app/knowledge/failure_db.py`'s `FixCache` already replays a previously-successful fix without an LLM call when a diagnostic set exactly matches one seen before. It already works well: `repair_db.json` currently holds 252 unique cached patterns backing 447 recorded successes — roughly 195 replays that skipped an LLM call entirely, including patterns reused 30x and 26x across clearly different generated apps.

That reuse happens because common scaffold files (`auth_routes.py`, `auth.py`, `seed_routes.py`, `RegisterPage.jsx`, `LoginPage.jsx`) tend to produce near-identical diagnostic text across unrelated apps — the content is boilerplate, not idea-specific. Exact-hash matching already captures this. The remaining gap is narrower than "repair memory doesn't generalize": it's the small number of near-duplicate diagnostics that differ only in incidental noise (a line number, an HTTP status code, an error count, a trivial change in which extra diagnostic co-occurred in the group) and therefore miss the exact hash despite being the same underlying, already-solved failure.

## Rejected hypothesis (tested before implementation)

The original design for this experiment proposed normalizing diagnostic messages by stripping quoted identifiers (`'Response'`, `'users'`, etc.) to generalize across apps. Before writing any code, this was checked against real data: analyzing `generation_log.jsonl`'s 278 recorded runs (441 dominant-error mentions, 176 unique messages) for what a quoted-identifier-stripping normalizer would merge.

Result: of 5 normalized signatures that merged distinct raw messages, 2 were benign (`Edit entity: 405` vs `422`; `Transform failed with 6/7/9 errors` — same remediation regardless of the number) but **3 were dangerous**:

- `AttributeError: type object 'Response'/'Donation'/'HabitLog' has no attribute 'create...'` — three different model classes needing three different fixes, merged into one bucket by stripping the class name.
- `Undefined symbol 'User'/'func'/'List' in ...` — a missing model import, a missing SQLAlchemy import, and a missing typing import, merged the same way.

**Conclusion: quoted identifiers are part of the root cause, not noise, and must never be stripped.** This finding directly shaped the design below — normalization is deliberately narrow.

**Proxy-analysis numbers, preserved for future reference:**

| Metric | Value |
|---|---|
| Generation runs analyzed (`generation_log.jsonl`) | 278 |
| Total `dominant_errors` mentions | 441 |
| Unique raw messages | 176 |
| Unique normalized signatures (quoted-identifier-stripping scheme) | 153 |
| Normalized signatures merging >1 distinct raw message | 5 |
| — of which benign (same remediation regardless of the merged value) | 2 |
| — of which unsafe (different root cause, different fix) | 3 |

If a future contributor asks "why not also normalize model/symbol names for broader reuse," this table is the answer: it was tried, in effect, against real data, and 3 of 5 resulting merges were between failures needing different fixes.

## Why this is not semantic matching

This experiment intentionally avoids semantic or embedding-based matching. It only tolerates incidental numeric variation (line numbers, HTTP status codes, counts, ids) while preserving identifiers, file paths, categories, and file context exactly. Generalization across different symbols, models, imports, or other application-specific diagnostic content remains explicitly out of scope — the proxy analysis above is the evidence for why, not just a stated preference.

## Current behavior (unchanged by this experiment)

- `app/repair/grouper.py`'s `group_diagnostics()` already clusters raw diagnostics into `DiagnosticGroup`s per repair round (by file, by category, by import-cascade), capping at `max_groups=8`. This experiment does not touch grouping.
- `orchestrator.py`'s `_apply_fix_group()` calls `fix_cache.lookup(group.diagnostics)` (exact match) before ever calling the LLM. This experiment adds a second, stricter-gated call *after* that one misses — the exact-match path is completely unchanged.
- Every fix (cache-sourced or LLM-sourced) is subject to the existing snapshot/regression safety net (`orchestrator.py` lines ~1157-1291): a fix is applied, verified, and reverted via `_ProjectSnapshot.revert()` if it introduces regressions with no score gain. A cache entry is only (re-)stored if the group's own `error_id`s are confirmed cleared from post-fix diagnostics (lines ~1297-1327). This experiment relies entirely on that existing mechanism as its correctness backstop — it does not add a new one.

## Proposed algorithm

**Normalization — digits only:**
```python
def _normalize_message(msg: str) -> str:
    msg = re.sub(r"\d+", "<N>", msg)   # line numbers, HTTP codes, counts, ids
    return re.sub(r"\s+", " ", msg).strip()
```
Quoted identifiers, file paths, and all other text are left untouched. This is deliberately much narrower than a general fuzzy-match — it is "the same message modulo incidental numbers," not "a semantically similar message."

**Composite key, not a standalone hash:**
```python
key = sha256(f"{category}|{file_basename_or_none}|{normalized_message}")
```
Composed per-diagnostic, then the group's key is the sorted+joined set of per-diagnostic keys, hashed the same way `_diagnostic_hash` already sorts+joins raw messages today. Keying on `(category, file_basename, normalized_message)` rather than the normalized message alone means that even if the normalization function is later found to be too aggressive in some category, the category/file components still constrain matches — no single weak point.

For diagnostics with no `file_path` (import cascades, grouped in `grouper.py` by missing-module name), `file_basename` is omitted from the key; since the module name itself is never stripped by normalization, two different missing modules still produce different keys without any special-casing.

## Eligibility gates (fail-closed)

`FixCache.lookup_fuzzy()` is only consulted after the existing exact `lookup()` misses, and only returns a hit when **all** of:

1. Every diagnostic in the group either has `file_path` in a fixed allowlist (`app/routes/auth_routes.py`, `app/utils/auth.py`, `app/routes/seed_routes.py`, `src/pages/RegisterPage.jsx`, `src/pages/LoginPage.jsx`) or is an IMPORT-category cascade diagnostic with no file_path. A group with any diagnostic outside this set is entirely ineligible — no partial matching.
2. The candidate cache entry has `success_count >= 2` under its own composite key (i.e., already proven at least twice, not a one-off).

Not expanding beyond this allowlist in v1 (no CRUD routers, models, middleware, or generated components yet) — that expansion is explicitly deferred to a follow-up experiment, gated on this one's measured results.

## Data model changes (additive, backward-compatible)

`CachedFix` gains new optional fields; existing 252 entries load unchanged via dataclass defaults:

```python
@dataclass
class CachedFix:
    fix_hash: str
    fix_content: dict
    success_count: int = 1
    first_seen: str = ""
    last_seen: str = ""
    source_idea: str = ""
    # New in Exp133:
    category: str = ""
    file_basename: str = ""
    normalized_signature: str = ""
    files_changed: list = field(default_factory=list)
    imports_added: list = field(default_factory=list)
    symbols_added: list = field(default_factory=list)
```

`imports_added`/`symbols_added` are populated at `store()` time by diffing import statements between the pre-fix snapshot content (already captured by `_ProjectSnapshot`, no extra file reads) and the new `fix_content` — `ast`-based diff for `.py` files, regex-based for `.jsx`. Best-effort: any parse failure yields empty lists and never blocks the store. **Not consumed by any code in this experiment** — this is explicitly building a richer repair corpus for a possible future move from whole-file replay to patch/diff replay, deferred as noted below.

No separate index file: the normalized-key reverse map is rebuilt in memory from `self._data` at load time (252 entries — trivial cost).

## Telemetry

Reuses the existing `GenerationRecord.prevention_counts` dict (no new metrics pipeline):
- `fix_cache_fuzzy_hit` — incremented whenever `lookup_fuzzy()` returns a hit and its content is applied.
- `fix_cache_fuzzy_success` — incremented when that group's `error_id`s are confirmed cleared post-fix (same gate already used to decide whether to (re-)store).
- `fix_cache_fuzzy_failed` — incremented when they are not (reverted, or the group's errors persisted).

Fuzzy replay precision = `fuzzy_success / (fuzzy_success + fuzzy_failed)`.

The existing `[fix] Cache HIT ...` print gains an `(exact)`/`(fuzzy)` tag.

## Rollback plan

The entire fuzzy tier is one additional call site (`lookup_fuzzy()`) behind a single module-level flag in `failure_db.py` (`_FUZZY_MATCH_ENABLED = True`). Disabling it is a one-line change with zero blast radius elsewhere — the exact-match path, `grouper.py`, and the snapshot/revert mechanism are all untouched by this experiment regardless of whether the flag is on.

## Validation plan

1. **Offline, already done (see "Rejected hypothesis" above).** Retroactive collision analysis on the existing 252 `repair_db.json` entries is *not possible* — the store has only ever persisted the opaque `fix_hash`, never the raw diagnostic text behind it, so there is nothing to recompute a normalized signature from for entries written before this ships. The `generation_log.jsonl` proxy analysis above is the best available pre-implementation evidence and is what drove the normalization design.
2. **Unit tests** (`backend/tests/reliability/test_exp133_fuzzy_fix_cache.py`): normalization strips only digits and preserves the three real dangerous-collision examples as distinct; composite-key construction; `lookup_fuzzy` eligibility gating (allowlist enforcement, `success_count` threshold, fail-closed on any ineligible diagnostic in the group); import/symbol diff extraction on a synthetic before/after file pair. All offline, $0 cost, no network calls.
3. **Canary** (`run_canary.py --no-deploy`, fixed 3-app suite): the fuzzy tier is unlikely to fire at all within one run of the same 3 fixed apps (they already hit the exact-match cache). The canary's job here is to confirm the exact-match path and overall score are unaffected — a regression check, not a demonstration of fuzzy uplift.
4. **Prospective measurement**: real signal on hit rate and precision accrues over subsequent live generation runs, already captured automatically in `generation_log.jsonl`'s `prevention_counts` with no additional infrastructure. Evaluate against the success/kill criteria below after roughly 200-300 generated apps.

## Success criteria

- Zero regressions in canary runs attributable to a fuzzy replay.
- Fuzzy replay precision ≥ 95% (`fuzzy_success / (fuzzy_success + fuzzy_failed)`).
- A measurable (if modest) reduction in LLM repair calls over a larger benchmark run.
- No decrease in final ForgeScore compared to the exact-cache-only baseline.

## Kill criteria (defined up front, not after the fact)

- If the fuzzy hit rate is under 1% over the first 200-300 generated apps, retire the feature — the complexity isn't earning its keep.
- If it saves no meaningful number of LLM calls, remove it rather than carry dead complexity that is technically correct but practically irrelevant.
- If replay precision drops below 95%, tighten the eligibility gate (smaller allowlist, higher `success_count` threshold) or disable the flag from "Rollback plan" until it does.

## Explicitly out of scope for this experiment

- Expanding the allowlist beyond the 5 listed scaffold files (CRUD routers, models, middleware, arbitrary generated components) — a possible follow-up, gated on this one's measured results.
- Consuming `imports_added`/`symbols_added`/`files_changed` for anything — stored now, used later.
- Moving from whole-file replay to AST-diff/patch-based replay — a larger, separate direction that generalizes better long-term but is a multi-cycle project of its own, not this one.
