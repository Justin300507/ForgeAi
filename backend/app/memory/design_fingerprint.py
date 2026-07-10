"""
Design Fingerprint Tracking

Records the (category, style) pair every generation lands on, so ForgeAI
can tell whether it's producing genuine visual diversity across apps or
quietly collapsing onto the same few looks. Deliberately does NOT change
style_system.select_style()'s deterministic idea->style mapping — that
mapping's "same idea always gets the same style, stable across Check & Fix
re-runs" contract is load-bearing (documented in style_system.py) and must
never depend on generation history, or a repair re-run days later could
silently reskin an app out from under a user who never touched the design.

Instead, recent_pair_frequency() feeds a soft prompt NUDGE (see
design_system.py's build_design_system_injection) when a (category, style)
pair is currently overrepresented -- the assigned style's rules still apply,
only the specific accent/detail choices inside those rules are asked to
vary. The deterministic selection itself is untouched.
"""
import json
import os
from datetime import datetime, timezone

_LOG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "failure_memory", "design_fingerprints.json",
)
_WINDOW = 20  # how many recent generations count toward "overrepresented"
_OVERREP_THRESHOLD = 0.35  # a pair used in >35% of the last _WINDOW generations is "hot"


def _load() -> list[dict]:
    if not os.path.exists(_LOG_PATH):
        return []
    try:
        with open(_LOG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save(entries: list[dict]) -> None:
    try:
        os.makedirs(os.path.dirname(_LOG_PATH), exist_ok=True)
        with open(_LOG_PATH, "w", encoding="utf-8") as f:
            json.dump(entries[-500:], f, indent=2)  # cap file growth
    except Exception:
        pass


def record_design(project_name: str, category_key: str, style_key: str) -> None:
    """Append one generation's (category, style) pair. Never raises --
    telemetry must not be able to break a generation run."""
    try:
        entries = _load()
        entries.append({
            "project_name": project_name,
            "category": category_key,
            "style": style_key,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        _save(entries)
    except Exception:
        pass


def recent_pair_frequency(category_key: str, style_key: str) -> float:
    """Fraction of the last _WINDOW recorded generations that used this
    exact (category, style) pair. Returns 0.0 on any error or empty log --
    fails toward "not overrepresented" so a telemetry hiccup never triggers
    an unnecessary diversity nudge."""
    try:
        entries = _load()
        if not entries:
            return 0.0
        recent = entries[-_WINDOW:]
        matches = sum(1 for e in recent if e.get("category") == category_key and e.get("style") == style_key)
        return matches / len(recent)
    except Exception:
        return 0.0


def is_overrepresented(category_key: str, style_key: str) -> bool:
    return recent_pair_frequency(category_key, style_key) > _OVERREP_THRESHOLD
