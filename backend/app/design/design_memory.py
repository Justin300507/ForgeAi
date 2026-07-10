"""
Design Memory (V19).

Every generation saves a full design record (identity, layout, motion,
typography, component mix, density, navigation, hero style, palette,
interaction style, experience flow). Before the next generation, the new
app's planned record is compared against recent memory:

    similarity vs the most similar RECENT, DIFFERENT-idea record
        low  (< threshold)  ->  safe, generate as planned
        high (>= threshold) ->  inject a NEW DIRECTION directive — concrete,
                                deterministic variations WITHIN the assigned
                                style's rules

so ForgeAI gradually stops repeating itself without any user configuration.

Two contracts this module must never break:
1. The deterministic idea->category/style/layout mapping is untouched —
   memory only shapes prompt-level variation, never axis selection
   (same reasoning as design_fingerprint.py's original nudge).
2. Records of the SAME idea are excluded from similarity — a Check & Fix
   re-run of an existing app compares only against OTHER apps, so an app
   can never trigger a new direction against its own history.
"""
import hashlib

from app.design.brief import DesignBrief

SIMILARITY_THRESHOLD = 0.75
_WINDOW = 20

# Dimension weights for the similarity score. component_mix uses Jaccard
# overlap; every other dimension is exact-match. Weights sum to 1.0.
_WEIGHTS = {
    "palette": 0.20,
    "style": 0.20,
    "typography": 0.15,
    "layout": 0.10,
    "category": 0.10,
    "hero_style": 0.05,
    "density": 0.05,
}
_COMPONENT_MIX_WEIGHT = 0.15


def _idea_hash(idea: str) -> str:
    return hashlib.md5(idea.strip().lower().encode("utf-8")).hexdigest()[:12]


def build_design_record(brief: DesignBrief, ds: dict, project_name: str = "") -> dict:
    """The full design fingerprint of one generation — the dimensions the
    user-facing design identity is actually made of."""
    from app.prompts.component_library import select_components
    from app.prompts.style_system import STYLES
    style = STYLES.get(brief.style_key, {})
    shell = brief.layout.shell

    if brief.analysis.device_priority == "mobile-first":
        interaction = "touch-first: generous tap targets, thumb-reachable primary actions"
    elif brief.analysis.posture in ("prosumer", "enterprise") and brief.analysis.usage_frequency == "daily":
        interaction = "keyboard-first: command palette, focus rings, hinted shortcuts"
    else:
        interaction = "pointer-first: hover states carry the affordances"

    motion = (style.get("motion_tier") or "base motion tokens, standard 40ms stagger")
    return {
        "design_id": hashlib.md5(f"{project_name}|{brief.idea}".encode("utf-8")).hexdigest()[:12],
        "idea_hash": _idea_hash(brief.idea),
        "category": brief.category_key,
        "style": brief.style_key,
        "layout": shell,
        "navigation": "top nav bar" if shell == "topnav" else "gradient sidebar",
        "motion": motion.split("—")[0].split(".")[0].strip()[:80],
        "typography": style.get("font_heading", "Inter"),
        "component_mix": sorted(select_components(ds, brief.style_key)),
        "density": brief.analysis.data_density,
        "hero_style": ("editorial page hero (oversized title + subtitle + action)"
                       if shell == "topnav" else "greeting header + stats grid"),
        "palette": ds.get("primary_name", ""),
        "interaction_style": interaction,
        "experience_flow": brief.experience.first_screen.split(" — ")[0][:120],
        "posture": brief.analysis.posture,
        "font_heading": style.get("font_heading", "Inter"),
        "shell": shell,
        "data_density": brief.analysis.data_density,
    }


def similarity(a: dict, b: dict) -> float:
    """Weighted similarity between two design records, 0.0-1.0. A missing
    dimension on either side counts as different (older, pre-V19 records
    naturally score low, which is the safe direction)."""
    score = 0.0
    for dim, weight in _WEIGHTS.items():
        va, vb = a.get(dim), b.get(dim)
        if va is not None and va == vb:
            score += weight
    mix_a, mix_b = set(a.get("component_mix") or []), set(b.get("component_mix") or [])
    if mix_a and mix_b:
        score += _COMPONENT_MIX_WEIGHT * (len(mix_a & mix_b) / len(mix_a | mix_b))
    return round(score, 4)


def most_similar_recent(record: dict, entries: list[dict]) -> tuple[float, dict | None]:
    """Highest similarity against the last _WINDOW records with a DIFFERENT
    idea (contract #2 above)."""
    candidates = [e for e in entries[-_WINDOW:]
                  if e.get("idea_hash") != record.get("idea_hash")]
    best_score, best = 0.0, None
    for e in candidates:
        s = similarity(record, e)
        if s > best_score:
            best_score, best = s, e
    return best_score, best


# Concrete within-style variations. Selection is deterministic per idea so
# the directive itself is stable for a given (idea, memory state).
_VARIATIONS = [
    "Lead the main page with the SECOND signature component from the design "
    "system's list instead of the first — make it the hero widget, with the "
    "usual stats treated as secondary.",
    "Invert accent emphasis: use the darker end of the brand palette "
    "(the primary_dark token) as the dominant accent and reserve the full "
    "gradient strictly for the single primary CTA per page.",
    "Change the dashboard's composition rhythm: instead of the uniform "
    "4-equal-stat-cards row, open with one double-width feature card (the "
    "most important metric or the chart) flanked by two compact stats.",
    "Give list pages a different personality: lead with the tabbed status "
    "split (All / Active / Archived) above the list instead of the plain "
    "search-bar-first header used by default.",
    "Vary the icon vocabulary: draw primarily from the SECOND half of the "
    "design system's icon list, so cards and nav don't repeat the icons "
    "recent apps led with.",
]


def divergence_directive(idea: str, ds: dict) -> str:
    """The NEW DIRECTION prompt block, or "" when the planned design is
    already distinct from recent memory. Never raises — memory must not be
    able to break a generation."""
    try:
        from app.design.brief import compose_design_brief
        from app.memory.design_fingerprint import load_recent
        record = build_design_record(compose_design_brief(idea), ds)
        score, nearest = most_similar_recent(record, load_recent(_WINDOW))
        if score < SIMILARITY_THRESHOLD or nearest is None:
            return ""

        digest = int(hashlib.md5(idea.strip().lower().encode("utf-8")).hexdigest(), 16)
        first = digest % len(_VARIATIONS)
        second = (first + 1 + digest // 7 % (len(_VARIATIONS) - 1)) % len(_VARIATIONS)
        chosen = [_VARIATIONS[first], _VARIATIONS[second if second != first else (first + 1) % len(_VARIATIONS)]]
        variations = "\n".join(f"  {i + 1}. {v}" for i, v in enumerate(chosen))
        overlap = [dim for dim in _WEIGHTS if record.get(dim) and record.get(dim) == nearest.get(dim)]

        return f"""
NEW DIRECTION REQUIRED — design memory found this app {int(score * 100)}% similar
to a recently generated app (shared: {", ".join(overlap)}). Stay fully within
the assigned style's rules above (never switch styles, colors stay this
category's tokens), but apply BOTH of these concrete composition changes so
this app reads as its own product, not a re-skin:
{variations}
"""
    except Exception:
        return ""
