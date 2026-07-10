"""
Design Brief — the assembled output of the design-intelligence pipeline.

compose_design_brief(idea) runs the deterministic agent chain (product
analysis -> experience composition -> layout planning -> inspiration
synthesis) on top of the existing category/style axes and returns one
dataclass every downstream consumer reads: the prompt renderer, the
frontend critic, and the fingerprint tracker.
"""
from dataclasses import dataclass

from app.design.experience import ExperiencePlan, compose_experience
from app.design.layout import LayoutPlan, plan_layout
from app.design.product_analysis import ProductAnalysis, analyze_product


@dataclass(frozen=True)
class DesignBrief:
    idea: str
    category_key: str
    style_key: str
    analysis: ProductAnalysis
    experience: ExperiencePlan
    layout: LayoutPlan
    inspiration: list[str]

    def fingerprint(self) -> dict:
        """The design-diversity fingerprint dimensions this generation lands
        on — recorded by design_fingerprint.record_design so ForgeAI can see
        whether it is actually producing varied identities."""
        from app.prompts.style_system import STYLES
        style = STYLES.get(self.style_key, {})
        return {
            "shell": self.layout.shell,
            "font_heading": style.get("font_heading", "Inter"),
            "posture": self.analysis.posture,
            "data_density": self.analysis.data_density,
        }


def compose_design_brief(idea: str) -> DesignBrief:
    from app.prompts.design_system import detect_category_key
    from app.prompts.style_system import select_style

    category_key = detect_category_key(idea)
    style_key = select_style(idea)
    analysis = analyze_product(idea, category_key)

    from app.design.inspiration import select_inspiration
    return DesignBrief(
        idea=idea,
        category_key=category_key,
        style_key=style_key,
        analysis=analysis,
        experience=compose_experience(category_key),
        layout=plan_layout(category_key),
        inspiration=select_inspiration(analysis, style_key),
    )
