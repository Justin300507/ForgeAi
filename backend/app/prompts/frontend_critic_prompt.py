def build_frontend_critic_prompt(
    files: dict,
    idea: str,
    category_label: str,
    style_label: str,
    signature_components: list[str],
    shell: str = "sidebar",
    experience_goal: str = "",
    motion_intensity: str | None = None,
    include_landing_page: bool = False,
) -> str:
    file_blocks = "\n\n".join(
        f"=== {path} ===\n{content[:2500]}"
        for path, content in list(files.items())[:16]
    )
    components_str = ", ".join(signature_components) or "(none specified)"
    shell_note = (
        "top-nav content shell (sticky header + centered max-w column — this app "
        "was DESIGNED without a sidebar; do NOT flag the missing sidebar as an issue)"
        if shell == "topnav"
        else "dark gradient sidebar shell"
    )
    experience_note = (
        f"\nExperience goal for the first screen: {experience_goal}" if experience_goal else ""
    )
    intensity = motion_intensity or "moderate"
    motion_check_note = (
        f"\nThis app's user-selected MOTION INTENSITY is \"{intensity}\" — "
        + (
            "verify real `motion` (motion/react) usage actually appears where "
            "specified (AnimatePresence route transitions, whileInView reveals, "
            "whileHover/whileTap on interactive elements), not just the base "
            "Tailwind animate-* tokens; missing it at this tier is a real gap, "
            "not a nitpick."
            if intensity == "heavy"
            else "the base Tailwind motion tokens are sufficient at this tier — "
            "do NOT flag the absence of the `motion` JS library as an issue."
        )
    )
    landing_page_note = (
        "\nThis app was told to generate a public LandingPage.jsx at \"/\" "
        "(hero, feature highlights, CTA to signup) — verify it actually exists "
        "and reads as a real marketing page, not a stub; its absence is a "
        "real gap here, not optional."
        if include_landing_page else ""
    )

    return f"""You are a senior product designer reviewing a generated React +
Tailwind frontend, the way you would review a junior designer's PR before it
ships — not a code linter, a DESIGN reviewer. This app is supposed to be:
{idea[:200]}

Assigned category: {category_label}
Assigned visual style: {style_label}
Assigned app shell: {shell_note}
Signature components this app was told to include somewhere: {components_str}{experience_note}{motion_check_note}{landing_page_note}

PROJECT FILES:
{file_blocks}

Evaluate these four dimensions (0-100 each), the same way you'd judge
whether this could ship at a company like Linear, Stripe, or Vercel instead
of reading as a templated CRUD scaffold:

1. VISUAL HIERARCHY (hierarchy_score):
   - Does type weight/size clearly separate page titles, section headings,
     labels, and body text — or does everything look the same weight?
   - Do the most important numbers/actions on each page actually draw the
     eye first?

2. DESIGN SYSTEM COMPLIANCE (compliance_score):
   - Are the category's actual brand tokens used (gradient, sidebar colors,
     icon badges), or did generic indigo/slate leak through anywhere?
   - Are the motion tokens (animate-fade-in-up, animate-scale-in, stagger
     delays, float-slow/slower blobs) actually present, not just on paper?
   - Is at least one of the signature components above actually built
     somewhere (not just a plain list+form pair standing in for it)?

3. POLISH & DEPTH (polish_score):
   - Card depth (shadow/ring/hover-lift), icon badge gradients, button
     press feedback — are these actually present, or flat/bare?
   - Empty states, loading skeletons, toast feedback — present and styled,
     or missing/plain?
   - MOTION QUALITY: do lists stagger in, do dialogs/toasts scale in, does
     the app follow its style's motion intensity — or does everything
     appear instantly / animate identically regardless of context?
   - Does this look "premium" or does it read as a wireframe with colors
     filled in?

4. CONSISTENCY (consistency_score):
   - Do all pages share the same spacing rhythm, corner radius, shadow
     depth — or does one page look like a different app?
   - Are icon sizes, badge shapes, and card patterns reused consistently
     across pages instead of reinvented per-page?
   - ORIGINALITY: does this app have its own identity (domain-specific
     components, the assigned style genuinely applied) — or would swapping
     the accent color make it indistinguishable from any other CRUD app?

For each real issue, include: file, severity (critical|high|medium|low),
description (what's wrong, specifically), and a concrete fix instruction
written as if you were telling the original author exactly what to change —
specific enough that someone could apply it without seeing your reasoning
(e.g. "Replace the flat bg-white stat cards with the glass pattern: add
backdrop-blur-xl, ring-1 ring-black/5, and the gradient icon badge" — not
just "improve the cards").

Only flag files that are GENUINELY weak — a file that already follows the
design system well should not be flagged just to have something to say.

Return JSON ONLY (no markdown):
{{
  "hierarchy_score": <0-100>,
  "compliance_score": <0-100>,
  "polish_score": <0-100>,
  "consistency_score": <0-100>,
  "design_score": <weighted average, overall verdict>,
  "issues": [
    {{
      "severity": "high",
      "file": "src/pages/Dashboard.jsx",
      "description": "Stat cards are flat bg-white with no gradient icon badge or hover lift",
      "fix": "Apply the EXAMPLE STAT CARD pattern: backdrop-blur-xl, ring-1 ring-black/5, gradient stat_icon_bg badge, hover:-translate-y-0.5"
    }}
  ],
  "positives": ["list of things done well, be specific"],
  "summary": "one paragraph verdict — would this ship at a premium product company as-is?"
}}"""
