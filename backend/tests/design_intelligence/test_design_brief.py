"""
Unit tests for the design-intelligence pipeline (app/design/*) and its
integration into the design-system injection. Plain assert-based -- run
directly:  python tests/design_intelligence/test_design_brief.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.design.brief import compose_design_brief
from app.design.layout import plan_layout
from app.design.render import render_brief_sections
from app.prompts.component_library import COMPONENTS, COMPONENT_META, build_component_reference
from app.prompts.design_system import CATEGORIES, build_design_system_injection, detect_category_key
from app.prompts.frontend_critic_prompt import build_frontend_critic_prompt
from app.prompts.style_system import STYLES, select_style

# One representative idea per category — each must actually detect as its
# category (keyword match), otherwise the test would silently exercise the
# default category 13 times.
IDEAS = {
    "fitness": "A gym workout tracker with exercise logging and progress charts",
    "finance": "A personal expense and budget tracker with spending insights",
    "productivity": "A todo and task planner with projects and deadlines",
    "social": "A community forum where users post, comment and follow friends",
    "crm": "A sales CRM with leads, deals, pipeline and contacts",
    "booking": "An appointment booking system with availability slots",
    "ecommerce": "An online store with products, cart, orders and inventory",
    "healthcare": "A clinic patient management app with appointments and prescriptions",
    "ai_saas": "An AI assistant workflow tool with prompts and automation",
    "restaurant": "A restaurant menu and table reservation app for diners",
    "travel": "A travel itinerary planner with destinations and trip budgets",
    "education": "An online course platform with lessons, quizzes and student progress",
    "portfolio": "A designer portfolio gallery with case studies and projects",
}


def _fenced_code_blocks(text: str) -> list[str]:
    return re.findall(r"```jsx\n(.*?)```", text, flags=re.DOTALL)


def test_ideas_detect_their_category():
    for expected, idea in IDEAS.items():
        got = detect_category_key(idea)
        assert got == expected, f"idea for {expected!r} detected as {got!r}"


def test_brief_is_deterministic():
    for idea in IDEAS.values():
        assert compose_design_brief(idea) == compose_design_brief(idea)


def test_layout_plan_assignments():
    for cat in CATEGORIES:
        plan = plan_layout(cat)
        if cat in ("restaurant", "travel", "portfolio"):
            assert plan.shell == "topnav", f"{cat} should be topnav"
        else:
            assert plan.shell == "sidebar", f"{cat} should be sidebar"


def test_brief_has_all_sections():
    for cat, idea in IDEAS.items():
        brief = compose_design_brief(idea)
        assert brief.category_key == cat
        assert brief.style_key in STYLES
        assert brief.analysis.audience and brief.analysis.tone
        assert brief.experience.first_screen and brief.experience.empty_state
        assert 2 <= len(brief.inspiration) <= 3, f"{cat}: {len(brief.inspiration)} principles"
        fp = brief.fingerprint()
        assert fp["shell"] in ("sidebar", "topnav")
        assert fp["font_heading"] and fp["posture"] and fp["data_density"]


def test_injection_every_category_brace_balanced_code():
    """The exact JSX-templating failure class that has repeatedly bitten
    this codebase: every fenced jsx block in every category's injection
    must have balanced braces after token substitution."""
    for cat, idea in IDEAS.items():
        injection = build_design_system_injection(idea)
        assert "EXPERIENCE BLUEPRINT" in injection, f"{cat}: brief block missing"
        assert "DESIGN PRINCIPLES" in injection, f"{cat}: principles missing"
        assert "{ds[" not in injection and "{brief" not in injection, f"{cat}: unsubstituted token"
        for i, block in enumerate(_fenced_code_blocks(injection)):
            opens, closes = block.count("{"), block.count("}")
            assert opens == closes, (
                f"{cat}: fenced block #{i} unbalanced ({opens} open / {closes} close):\n{block[:300]}"
            )


def test_injection_layout_override_only_for_topnav_categories():
    for cat, idea in IDEAS.items():
        injection = build_design_system_injection(idea)
        has_override = "LAYOUT OVERRIDE — TOP-NAV SHELL" in injection
        if cat in ("restaurant", "travel", "portfolio"):
            assert has_override, f"{cat}: topnav override missing"
            assert "PAGE HERO RULE" in injection
        else:
            assert not has_override, f"{cat}: unexpected topnav override"
            # The battle-tested sidebar example must remain untouched.
            assert "EXAMPLE SIDEBAR" in injection


def test_component_reference_formats_for_every_category_and_style():
    for cat in CATEGORIES.values():
        for style_key in list(STYLES) + [None]:
            ref = build_component_reference(cat, style_key=style_key)
            assert "{gradient}" not in ref and "{primary_name}" not in ref
            # ≤2 snippets by design (prompt-size budget)
            assert ref.count("```jsx") <= 2


def test_component_meta_covers_every_component():
    for key in COMPONENTS:
        meta = COMPONENT_META.get(key)
        assert meta, f"{key} missing metadata"
        for field in ("categories", "styles", "complexity", "motion", "a11y"):
            assert meta.get(field), f"{key} missing meta field {field!r}"


def test_new_components_selected_for_their_categories():
    ai_ref = build_component_reference(CATEGORIES["ai_saas"], style_key="glass")
    assert "Streaming chat" in ai_ref or "Command palette" in ai_ref
    rest_ref = build_component_reference(CATEGORIES["restaurant"], style_key="glass")
    assert "Photography-forward" in rest_ref


def test_render_brief_sections_content():
    brief = compose_design_brief(IDEAS["healthcare"])
    text = render_brief_sections(brief, CATEGORIES["healthcare"])
    assert "Extra accessibility emphasis" in text  # healthcare's stricter a11y bar
    brief_topnav = compose_design_brief(IDEAS["travel"])
    text_topnav = render_brief_sections(brief_topnav, CATEGORIES["travel"])
    assert "LAYOUT OVERRIDE — TOP-NAV SHELL" in text_topnav
    assert "max-w-6xl" in text_topnav


def test_critic_prompt_shell_awareness():
    files = {"src/App.jsx": "export default function App() {}"}
    p = build_frontend_critic_prompt(files, "a travel app", "Travel", "Glassmorphism", [],
                                     shell="topnav", experience_goal="wanderlust first")
    assert "do NOT flag the missing sidebar" in p
    assert "wanderlust first" in p
    p2 = build_frontend_critic_prompt(files, "a crm", "CRM", "Glassmorphism", [])
    assert "sidebar shell" in p2
    assert "MOTION QUALITY" in p2 and "ORIGINALITY" in p2


def test_fingerprint_recording_with_extra(tmp_path=None):
    import app.memory.design_fingerprint as dfp
    import json
    original = dfp._LOG_PATH
    test_path = os.path.join(os.path.dirname(__file__), "_test_fingerprints.json")
    try:
        dfp._LOG_PATH = test_path
        dfp.record_design("proj_a", "travel", "glass", extra={"shell": "topnav", "posture": "consumer"})
        dfp.record_design("proj_b", "crm", "bento")  # backward-compat: no extra
        with open(test_path, encoding="utf-8") as f:
            entries = json.load(f)
        assert entries[-2]["shell"] == "topnav"
        assert entries[-2]["posture"] == "consumer"
        assert entries[-1]["category"] == "crm" and "shell" not in entries[-1]
    finally:
        dfp._LOG_PATH = original
        if os.path.exists(test_path):
            os.remove(test_path)


def test_frontend_prompt_builds_for_topnav_and_sidebar():
    from app.prompts.frontend_prompt import build_frontend_prompt
    arch = {"project_name": "trip planner travel itinerary", "features": ["itinerary"],
            "api_endpoints": []}
    p = build_frontend_prompt(arch, idea=IDEAS["travel"])
    assert "LAYOUT OVERRIDE — TOP-NAV SHELL" in p
    assert "UNLESS a" in p  # base sidebar section acknowledges the override
    p2 = build_frontend_prompt({"project_name": "crm sales", "features": ["deals"],
                                "api_endpoints": []}, idea=IDEAS["crm"])
    # The base prompt's sidebar section references the override by name in its
    # unless-clause; the override BLOCK itself must not be present for sidebar apps.
    assert "this app has NO sidebar" not in p2


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  PASS {name}")
            except AssertionError as e:
                failures += 1
                print(f"  FAIL {name}: {e}")
    print(f"\n{'ALL PASS' if failures == 0 else f'{failures} FAILURE(S)'}")
    sys.exit(1 if failures else 0)
