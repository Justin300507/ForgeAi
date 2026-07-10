"""
Inspiration Engine (deterministic).

A curated library of design PRINCIPLES synthesized from the products and
systems ForgeAI aspires to rival (Linear, Stripe, Apple, Vercel, Notion,
Arc, premium editorial sites) and from pattern sources like shadcn/ui,
Magic UI, Aceternity, Tremor, and Origin UI. Deliberately principles, not
copies: each entry teaches a transferable pattern in one or two sentences,
and selection composes 2-3 that fit this product's analysis + style. No
product names appear in the injected prompt text — the generator should
internalize the pattern, not imitate a brand.
"""
from app.design.product_analysis import ProductAnalysis

# key -> (principle text, predicate inputs it suits)
_PRINCIPLES: dict[str, str] = {
    "calm_density": (
        "Dense-tool calm: when a screen carries many rows, earn clarity through "
        "rhythm, not chrome — identical row heights, one accent color reserved for "
        "state, muted borders, and generous line-height. The busier the data, the "
        "quieter the surface."
    ),
    "gradient_restraint": (
        "Gradient restraint: the brand gradient appears only where attention "
        "belongs — the primary CTA, the brand mark, one hero moment per page. "
        "Everything else stays in disciplined neutrals, which is what makes the "
        "gradient feel premium instead of decorative."
    ),
    "modular_grid": (
        "Modular grid confidence: vary card sizes deliberately (one hero-sized "
        "card among regular ones) while keeping perfect alignment to a single "
        "grid — variety inside order reads as designed; variety without alignment "
        "reads as accidental."
    ),
    "monochrome_focus": (
        "Monochrome focus: build the interface almost entirely from the neutral "
        "scale and let the accent color mean something — state changes, live "
        "activity, the one thing happening now."
    ),
    "editorial_air": (
        "Editorial air: oversized display type, images that bleed to their "
        "container edges, and whitespace treated as a material. Captions and "
        "metadata sit small and quiet under confident headlines."
    ),
    "tactile_feedback": (
        "Tactile feedback: every interactive element acknowledges touch — press "
        "compression on buttons, lift on hoverable cards, a settle animation when "
        "items land. Users should feel the interface respond, not just see it."
    ),
    "keyboard_first": (
        "Keyboard-first power: daily-use tools reward fluency — a Cmd+K command "
        "palette, visible focus rings, and hinted shortcuts on primary actions "
        "make experts feel the product was built for them."
    ),
    "progressive_reveal": (
        "Progressive reveal: content enters as it becomes relevant — staggered "
        "list entrances, charts that draw in, sections that fade up on first "
        "paint. Motion narrates loading instead of hiding it."
    ),
    "honest_states": (
        "Honest states: loading, empty, error, and success are designed screens, "
        "not afterthoughts — shaped skeletons that match the content they "
        "replace, empty states that teach the first action, errors that say what "
        "to do next."
    ),
    "warm_texture": (
        "Warm texture: soft shadows, rounded geometry, and tinted (never pure "
        "white) backgrounds make utilitarian screens feel hospitable — warmth "
        "comes from surface treatment, not from adding decoration."
    ),
}

# Which principles suit which posture/density/style. Order matters — the
# first two matched are the strongest fit; a third is added if distinct.
_STYLE_AFFINITY: dict[str, list[str]] = {
    "glass": ["gradient_restraint", "progressive_reveal", "honest_states"],
    "bento": ["modular_grid", "monochrome_focus", "tactile_feedback"],
    "neubrutalist": ["tactile_feedback", "monochrome_focus", "honest_states"],
    "soft_clay": ["warm_texture", "tactile_feedback", "honest_states"],
    "minimal_editorial": ["editorial_air", "monochrome_focus", "honest_states"],
}


def select_inspiration(analysis: ProductAnalysis, style_key: str) -> list[str]:
    """Compose 2-3 principle texts fitting this product. Deterministic."""
    keys: list[str] = []

    # Density/posture lead: dense prosumer/enterprise tools get calm + keyboard.
    if analysis.data_density == "high":
        keys.append("calm_density")
    if analysis.posture in ("prosumer", "enterprise") and analysis.usage_frequency == "daily":
        keys.append("keyboard_first")
    if analysis.data_density == "low":
        keys.append("editorial_air")

    for k in _STYLE_AFFINITY.get(style_key, []):
        if k not in keys:
            keys.append(k)
        if len(keys) >= 3:
            break

    return [_PRINCIPLES[k] for k in keys[:3]]
