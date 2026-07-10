"""
Brief renderer — turns a DesignBrief into the prompt sections appended to
the design-system injection. Kept deliberately compact: these sections
steer intent (who this is for, what moments matter, which principles to
follow); the concrete tokens/code patterns live in the sections the base
injection already provides.
"""
from app.design.brief import DesignBrief
from app.design.layout import build_topnav_override


def render_brief_sections(brief: DesignBrief, ds: dict) -> str:
    a = brief.analysis
    x = brief.experience
    principles = "\n".join(f"  {i + 1}. {p}" for i, p in enumerate(brief.inspiration))
    a11y_extra = f"\n  Extra accessibility emphasis: {a.accessibility_note}" if a.accessibility_note else ""

    sections = f"""
═══════════════════════════════════════════════════════
EXPERIENCE BLUEPRINT  ← design these moments, not just pages
═══════════════════════════════════════════════════════

Who this is for: {a.audience}.
Emotional tone: {a.tone}.
Posture: {a.posture} · data density {a.data_density} · {a.device_priority} · used {a.usage_frequency}.{a11y_extra}

  First 30 seconds: {x.first_screen}
  What builds trust: {x.trust}
  The delight moment: {x.delight}
  Empty states: {x.empty_state}
  Success feels like: {x.success}

These are product requirements, not mood-board notes — the delight moment
and the empty-state treatment above must actually exist in the generated
code (using the motion tokens already defined; never invent new keyframes).

DESIGN PRINCIPLES for this app (follow these when composing every page):
{principles}
"""

    if brief.layout.shell == "topnav":
        sections += build_topnav_override(ds)
        sections += f"\nPAGE HERO RULE: {brief.layout.hero}\n"

    return sections
