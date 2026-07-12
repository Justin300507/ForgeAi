"""
Failed-strategy memory for the fix loop's escalation ladder.

RetryManager's ladder is currently blind: attempt 1 and 2 both route through
PATCH_FILE regardless of whether PATCH_FILE has ever fixed this KIND of
failure before. If a given (pattern, strategy) pair has failed repeatedly
across past runs, retrying it again just burns an attempt slot and an LLM
call before reaching a strategy that might actually work.

This module tracks per-(pattern_id, strategy) outcomes across all runs and
lets RetryManager skip a strategy that has a proven track record of failure
for the CURRENT failure pattern, escalating straight to the next rung of the
ladder instead.

Storage: backend/failure_memory/strategy_outcomes.json (human-readable,
git-trackable, same convention as failure_memory/patterns.json).
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Optional

_STORE_PATH = Path(__file__).parent.parent.parent / "failure_memory" / "strategy_outcomes.json"

# Matches recognizable error-type tokens embedded in diagnostic messages
# (Python exception class names, or the *Mismatch/*Violation vocabulary
# already used by failure_memory.patterns.json), e.g. "SQLAlchemyError",
# "ModuleNotFoundError", "RouterExportMismatch". Falls back to the
# diagnostic's ErrorCategory when no such token is present.
_ERROR_TYPE_RE = re.compile(r"\b([A-Z][A-Za-z0-9]*(?:Error|Exception|Mismatch|Violation))\b")

# Exp081: bump a strategy's number here when a numbered experiment
# materially changes that strategy's own implementation -- the same
# moment its experiments.md entry gets written, not a separate ritual.
# A strategy not listed here defaults to _DEFAULT_GENERATION, which is
# also what every entry recorded before this mechanism existed implicitly
# is (no "generation" field at all) -- so a strategy nobody bumps is
# byte-for-byte unaffected by any of the migration logic below.
#
# regenerate_module -> 2: Exp080 proved (via strategy_outcomes.json's own
# git history) that its 0-success blacklist for contract/api/SyntaxError
# was recorded entirely before ef9eebc's syntax-validation gate (2026-07-11)
# and Exp078's endpoint-preservation wiring (2026-07-12) -- i.e. under an
# implementation that no longer exists.
_STRATEGY_GENERATIONS: dict[str, int] = {
    "regenerate_module": 2,
}
_DEFAULT_GENERATION = 1


def _current_generation(strategy: str) -> int:
    return _STRATEGY_GENERATIONS.get(strategy, _DEFAULT_GENERATION)


def _migrate(data: dict) -> tuple[dict, bool]:
    """
    Reset any (pattern, strategy) entry whose recorded generation is older
    than that strategy's CURRENT generation -- a missing "generation" field
    (every entry recorded before this mechanism existed) is treated as
    _DEFAULT_GENERATION. Entries already on their strategy's current
    generation are left completely untouched, not just their counts --
    the whole entry object -- so a strategy nobody has bumped never shows
    up in a diff. Resetting wipes ALL prior evidence for that (pattern,
    strategy) pair uniformly, successes included: the point is that the
    STRATEGY's implementation changed, so every prior data point (whether
    it happened to succeed or fail) was measuring code that no longer
    exists. Returns (data, whether anything changed) so the caller only
    pays for a _save() when a migration actually happened.
    """
    changed = False
    for strategies in data.values():
        for strategy, entry in strategies.items():
            current_gen = _current_generation(strategy)
            entry_gen = entry.get("generation", _DEFAULT_GENERATION)
            if entry_gen < current_gen:
                strategies[strategy] = {
                    "tries": 0,
                    "successes": 0,
                    "generation": current_gen,
                }
                changed = True
    return data, changed


def _load() -> dict:
    if not _STORE_PATH.exists():
        return {}
    try:
        data = json.loads(_STORE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    data, changed = _migrate(data)
    if changed:
        _save(data)
    return data


def _save(data: dict):
    _STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _STORE_PATH.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )


def dominant_pattern(ctx) -> Optional[str]:
    """
    Best-effort classification of the current context's dominant failure
    into a pattern id. This is a heuristic stand-in for the AppContract-
    based pattern ids proposed in the VNext report (§5.2) -- it reuses
    whatever error-type vocabulary is already present in diagnostic
    messages rather than requiring a new taxonomy up front.
    """
    diags = ctx.all_diagnostics()
    if not diags:
        return None
    counts: Counter[str] = Counter()
    for d in diags:
        m = _ERROR_TYPE_RE.search(d.message or "")
        counts[m.group(1) if m else d.category.value] += 1
    if not counts:
        return None
    return counts.most_common(1)[0][0]


def record_outcome(pattern_id: str, strategy: str, improved: bool):
    """Record whether `strategy` improved the score for `pattern_id`."""
    if not pattern_id:
        return
    data = _load()
    entry = data.setdefault(pattern_id, {}).setdefault(
        strategy, {"tries": 0, "successes": 0, "generation": _current_generation(strategy)}
    )
    entry["tries"] += 1
    if improved:
        entry["successes"] += 1
    # Stamp the current generation on every write (not just new entries) --
    # an entry that was already valid and untouched by _migrate() still
    # gets tagged the moment it's next written to, so it carries an
    # explicit generation going forward instead of relying on "missing
    # field" to mean _DEFAULT_GENERATION forever.
    entry["generation"] = _current_generation(strategy)
    _save(data)


def should_skip(pattern_id: Optional[str], strategy: str, min_tries: int = 3) -> bool:
    """
    True if `strategy` has been tried at least `min_tries` times for this
    exact `pattern_id` across all past runs and NEVER once improved the
    score -- i.e. it has a proven track record of being useless here.
    """
    if not pattern_id:
        return False
    data = _load()
    entry = data.get(pattern_id, {}).get(strategy)
    if not entry:
        return False
    return entry["tries"] >= min_tries and entry["successes"] == 0
