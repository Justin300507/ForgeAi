"""
V6 Product Manager Agent

Transforms a plain-English idea into a full ProductSpec with user stories,
acceptance criteria, feature priorities, and non-functional requirements.
The PM spec drives all downstream agents — better spec = better code.
"""
from dataclasses import dataclass, field
from app.providers.ai_provider import generate_content
from app.prompts.product_manager_prompt import build_product_manager_prompt
from app.utils.json_cleaner import extract_json


@dataclass
class UserStory:
    role: str
    want: str
    so_that: str
    acceptance_criteria: list[str] = field(default_factory=list)


@dataclass
class CoreFeature:
    name: str
    description: str
    priority: str  # must_have | should_have | nice_to_have


@dataclass
class ProductSpec:
    project_name: str
    display_name: str
    tagline: str
    target_users: list[str]
    problem_statement: str
    user_stories: list[UserStory]
    core_features: list[CoreFeature]
    non_functional_requirements: dict
    data_entities: list[dict]
    api_modules: list[str]
    tech_stack: dict
    out_of_scope: list[str]
    raw: dict = field(default_factory=dict)  # original LLM output

    def to_plan_dict(self) -> dict:
        """Convert to the format expected by the existing Architect."""
        return {
            "project_name": self.project_name,
            "display_name": self.display_name,
            "features": [f.description for f in self.core_features if f.priority == "must_have"],
            "tech_stack": self.tech_stack,
            "db_entities": [e["name"] for e in self.data_entities],
            "api_modules": self.api_modules,
            # V6 extras
            "user_stories": [
                {
                    "role": s.role,
                    "want": s.want,
                    "acceptance_criteria": s.acceptance_criteria
                }
                for s in self.user_stories
            ],
            "non_functional_requirements": self.non_functional_requirements,
            "data_entities_detail": self.data_entities,
            "out_of_scope": self.out_of_scope,
        }


def run_product_manager(idea: str, provider: str = "auto") -> ProductSpec:
    """
    Run the PM agent on a plain-English idea.
    Returns a ProductSpec with user stories, features, and requirements.
    """
    print("\n=== PRODUCT MANAGER (V6) ===")
    print(f"  Analyzing: {idea[:80]}")

    prompt = build_product_manager_prompt(idea)
    raw_text = generate_content(prompt, provider, max_tokens=4000, stage="product_manager")

    try:
        data = extract_json(raw_text)
    except Exception as e:
        print(f"  PM parse error: {e} — falling back to basic plan")
        slug = idea.lower().replace(" ", "_")[:30]
        data = {
            "project_name": slug,
            "display_name": idea[:50],
            "tagline": idea,
            "target_users": ["Users"],
            "problem_statement": idea,
            "user_stories": [],
            "core_features": [{"name": "Core", "description": idea, "priority": "must_have"}],
            "non_functional_requirements": {"authentication": "required"},
            "data_entities": [],
            "api_modules": [],
            "tech_stack": {"backend": "FastAPI", "database": "SQLite", "frontend": "React + Vite"},
            "out_of_scope": [],
        }

    stories = [
        UserStory(
            role=s.get("role", "user"),
            want=s.get("want", ""),
            so_that=s.get("so_that", ""),
            acceptance_criteria=s.get("acceptance_criteria", []),
        )
        for s in data.get("user_stories", [])
    ]

    features = [
        CoreFeature(
            name=f.get("name", ""),
            description=f.get("description", ""),
            priority=f.get("priority", "must_have"),
        )
        for f in data.get("core_features", [])
    ]

    spec = ProductSpec(
        project_name=data.get("project_name", "project"),
        display_name=data.get("display_name", idea[:40]),
        tagline=data.get("tagline", ""),
        target_users=data.get("target_users", []),
        problem_statement=data.get("problem_statement", ""),
        user_stories=stories,
        core_features=features,
        non_functional_requirements=data.get("non_functional_requirements", {}),
        data_entities=data.get("data_entities", []),
        api_modules=data.get("api_modules", []),
        tech_stack=data.get("tech_stack", {"backend": "FastAPI", "database": "SQLite"}),
        out_of_scope=data.get("out_of_scope", []),
        raw=data,
    )

    print(f"  Project: {spec.display_name}")
    print(f"  {len(spec.user_stories)} user stories | {len(spec.core_features)} features | {len(spec.data_entities)} entities")
    return spec
