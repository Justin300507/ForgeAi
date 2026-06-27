"""
V6 Tech Lead Agent

Reviews the Architect's plan against the PM spec.
Catches design flaws early, sets security requirements, enforces API consistency,
and outputs TechConstraints that the Backend Team MUST follow.
"""
from dataclasses import dataclass, field
from app.providers.ai_provider import generate_content
from app.prompts.tech_lead_prompt import build_tech_lead_prompt
from app.utils.json_cleaner import extract_json
from app.utils.llm_cache import get_cached, set_cached


@dataclass
class ArchIssue:
    severity: str   # critical | warning | suggestion
    issue: str
    fix: str


@dataclass
class TechConstraints:
    tech_review_summary: str
    architecture_issues: list[ArchIssue]
    security_requirements: dict
    pagination_required: list[str]
    validation_rules: list[dict]
    db_design_notes: list[str]
    approved_file_structure: dict
    api_contract_notes: list[str]
    performance_notes: list[str]
    raw: dict = field(default_factory=dict)

    def has_critical_issues(self) -> bool:
        return any(i.severity == "critical" for i in self.architecture_issues)

    def to_prompt_context(self) -> str:
        """Format constraints for injection into backend generation prompts."""
        lines = ["=== TECH LEAD CONSTRAINTS (MUST FOLLOW) ==="]

        if self.security_requirements:
            auth_eps = self.security_requirements.get("authenticated_endpoints", [])
            public_eps = self.security_requirements.get("public_endpoints", [])
            if auth_eps:
                lines.append(f"\nAUTHENTICATED endpoints (require Depends(oauth2_scheme) or get_current_user):")
                for ep in auth_eps[:10]:
                    lines.append(f"  - {ep}")
            if public_eps:
                lines.append(f"\nPUBLIC endpoints (no auth required):")
                for ep in public_eps[:5]:
                    lines.append(f"  - {ep}")

        if self.pagination_required:
            lines.append(f"\nPAGINATION REQUIRED on these endpoints (add skip: int = 0, limit: int = 20):")
            for ep in self.pagination_required[:8]:
                lines.append(f"  - {ep}")

        if self.validation_rules:
            lines.append("\nVALIDATION RULES:")
            for rule in self.validation_rules[:6]:
                lines.append(f"  - {rule.get('field')}: {rule.get('rule')}")

        if self.api_contract_notes:
            lines.append("\nAPI CONTRACT:")
            for note in self.api_contract_notes[:4]:
                lines.append(f"  - {note}")

        if self.performance_notes:
            lines.append("\nPERFORMANCE REQUIREMENTS:")
            for note in self.performance_notes[:4]:
                lines.append(f"  - {note}")

        return "\n".join(lines)


_EMPTY_CONSTRAINTS = TechConstraints(
    tech_review_summary="No tech review performed",
    architecture_issues=[],
    security_requirements={},
    pagination_required=[],
    validation_rules=[],
    db_design_notes=[],
    approved_file_structure={},
    api_contract_notes=[],
    performance_notes=[],
)


def run_tech_lead(product_spec: dict, architecture: dict, provider: str = "auto") -> TechConstraints:
    """
    Run the Tech Lead agent.
    Inputs: PM spec dict + Architect's architecture dict
    Returns: TechConstraints with security, pagination, validation requirements
    """
    print("\n=== TECH LEAD REVIEW (V6) ===")

    _ck = {"arch": architecture}
    _cached = get_cached("tech_lead", _ck)
    if _cached is not None:
        print("  [cache hit]")
        data = _cached
    else:
        prompt = build_tech_lead_prompt(product_spec, architecture)
        try:
            raw_text = generate_content(prompt, provider, max_tokens=3000, stage="tech_lead")
            data = extract_json(raw_text)
            set_cached("tech_lead", _ck, data)
        except Exception as e:
            print(f"  Tech Lead failed: {e} — skipping constraints")
            return _EMPTY_CONSTRAINTS

    issues = [
        ArchIssue(
            severity=i.get("severity", "warning"),
            issue=i.get("issue", ""),
            fix=i.get("fix", ""),
        )
        for i in data.get("architecture_issues", [])
    ]

    constraints = TechConstraints(
        tech_review_summary=data.get("tech_review_summary", ""),
        architecture_issues=issues,
        security_requirements=data.get("security_requirements", {}),
        pagination_required=data.get("pagination_required", []),
        validation_rules=data.get("validation_rules", []),
        db_design_notes=data.get("db_design_notes", []),
        approved_file_structure=data.get("approved_file_structure", {}),
        api_contract_notes=data.get("api_contract_notes", []),
        performance_notes=data.get("performance_notes", []),
        raw=data,
    )

    critical = [i for i in issues if i.severity == "critical"]
    warnings = [i for i in issues if i.severity == "warning"]

    print(f"  Summary: {constraints.tech_review_summary[:100]}")
    print(f"  Issues: {len(critical)} critical, {len(warnings)} warnings")
    if critical:
        for i in critical[:3]:
            print(f"  [CRITICAL] {i.issue}")
            print(f"    Fix: {i.fix}")

    return constraints
