"""
ForgeAI Design Intelligence package.

The frontend design pipeline, in the order the agents run:

    idea
      -> product_analysis.analyze_product     (who is this for, what tone)
      -> experience.compose_experience        (first 30 seconds, trust, delight)
      -> style_system.select_style            (visual identity axis — existing)
      -> design_system.detect_category        (domain/color axis — existing)
      -> layout.plan_layout                   (app shell archetype)
      -> inspiration.select_inspiration       (synthesized design principles)
      -> brief.compose_design_brief           (assembles all of the above)
      -> render.render_brief_sections         (prompt injection text)

Every stage here is a deterministic, $0-LLM-cost pure function of the idea
string — the same idea always composes the same brief (the same stability
contract style_system.select_style documents). The one LLM design agent in
the pipeline is the Frontend Critic (frontend_critic_service.py), which
reviews the generated result against this brief and drives the bounded UI
Polish pass.

Future agents (vision/screenshot critic, A/B design generation, brand-kit
generation) plug in as additional stages: compose_design_brief() returns a
plain dataclass, so a new agent either enriches the brief before rendering
or consumes it after generation — no existing stage needs to change.
"""
from app.design.brief import DesignBrief, compose_design_brief

__all__ = ["DesignBrief", "compose_design_brief"]
