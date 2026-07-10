"""
Frontend Critic Agent

Judges whether a generated frontend actually looks premium, not just
whether it runs. The existing llm_judge.py vision stage only fires when
something is already broken (console errors, network failures, workflow
failures) — a clean, successfully-running app with generic/flat styling
never gets evaluated for design quality at all. This closes that gap with
one additional review call, following the exact same pattern as
qa_reviewer_service.py / code_review_service.py / security_reviewer_service.py.

Outputs a FrontendCriticReport used in V6 scoring, plus a short list of
`polish_targets` (file, fix instruction) for genuinely weak files — the
orchestrator feeds these into the EXISTING generate_fix()/write_fix()
machinery (same as the validation fix loop) rather than inventing new
patch-writing code.
"""
from dataclasses import dataclass, field
from pathlib import Path
from app.providers.ai_provider import generate_content
from app.prompts.frontend_critic_prompt import build_frontend_critic_prompt
from app.utils.json_cleaner import extract_json


@dataclass
class DesignIssue:
    severity: str    # critical | high | medium | low
    file: str
    description: str
    fix: str


@dataclass
class FrontendCriticReport:
    hierarchy_score: int
    compliance_score: int
    polish_score: int
    consistency_score: int
    design_score: int
    issues: list[DesignIssue] = field(default_factory=list)
    positives: list[str] = field(default_factory=list)
    summary: str = ""
    skipped: bool = False
    skip_reason: str = ""

    @property
    def polish_targets(self) -> list[tuple[str, list[str]]]:
        """(file, [fix instructions]) pairs for critical/high issues only —
        the threshold for spending an extra LLM call to actually patch it,
        same severity bar the validation fix loop implicitly uses."""
        by_file: dict[str, list[str]] = {}
        for issue in self.issues:
            if issue.severity in ("critical", "high") and issue.file:
                by_file.setdefault(issue.file, []).append(f"{issue.description} — {issue.fix}")
        return list(by_file.items())


def _load_frontend_files(project_path: str, max_files: int = 16) -> dict:
    files = {}
    base = Path(project_path) / "src"
    if not base.exists():
        return files
    for jsx_file in sorted(base.rglob("*.jsx"))[:max_files]:
        try:
            rel = str(jsx_file.relative_to(Path(project_path))).replace("\\", "/")
            files[rel] = jsx_file.read_text(encoding="utf-8", errors="replace")
        except Exception:
            pass
    return files


def _salvage_truncated_json(raw: str):
    """Same recovery strategy as code_review_service.py — trims back to the
    last complete object boundary and closes whatever brackets are open."""
    import json as _json
    if not raw:
        return None
    start = raw.find("{")
    if start == -1:
        return None
    text = raw[start:]
    last = text.rfind("}")
    if last == -1:
        return None
    text = text[:last + 1]
    stack, in_str, esc = [], False, False
    for ch in text:
        if esc:
            esc = False
            continue
        if ch == "\\":
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch in "[{":
            stack.append(ch)
        elif ch in "]}" and stack:
            stack.pop()
    text += "".join("]" if b == "[" else "}" for b in reversed(stack))
    try:
        result = _json.loads(text)
        return result if isinstance(result, dict) else None
    except Exception:
        return None


def run_frontend_critic(
    project_path: str,
    idea: str,
    provider: str = "auto",
) -> FrontendCriticReport:
    """Run the Frontend Critic agent on a generated project's .jsx files."""
    print("\n=== FRONTEND CRITIC (V6) ===")

    files = _load_frontend_files(project_path)
    if not files:
        return FrontendCriticReport(
            hierarchy_score=0, compliance_score=0, polish_score=0, consistency_score=0,
            design_score=0, skipped=True, skip_reason="No .jsx files found",
        )

    from app.prompts.design_system import detect_category
    from app.prompts.style_system import STYLES, select_style
    ds = detect_category(idea)
    style_label = STYLES.get(select_style(idea), {}).get("label", "Glassmorphism")

    raw_text = ""
    try:
        prompt = build_frontend_critic_prompt(
            files, idea, ds["label"], style_label, ds.get("components", []),
        )
        raw_text = generate_content(prompt, provider, max_tokens=6000, stage="frontend_critic")
        data = extract_json(raw_text)
    except Exception as e:
        data = _salvage_truncated_json(raw_text)
        if data is None:
            print(f"  Frontend critic failed: {e}")
            return FrontendCriticReport(
                hierarchy_score=70, compliance_score=70, polish_score=70, consistency_score=70,
                design_score=70, skipped=True, skip_reason=str(e),
            )
        print(f"  Frontend critic response was truncated — salvaged partial JSON "
              f"({len(data.get('issues', []))} issue(s) recovered)")

    issues = [
        DesignIssue(
            severity=i.get("severity", "medium"),
            file=i.get("file", ""),
            description=i.get("description", ""),
            fix=i.get("fix", ""),
        )
        for i in data.get("issues", [])
    ]

    hierarchy = int(data.get("hierarchy_score", 70))
    compliance = int(data.get("compliance_score", 70))
    polish = int(data.get("polish_score", 70))
    consistency = int(data.get("consistency_score", 70))
    design_score = int(data.get("design_score", (hierarchy + compliance + polish + consistency) // 4))

    report = FrontendCriticReport(
        hierarchy_score=hierarchy,
        compliance_score=compliance,
        polish_score=polish,
        consistency_score=consistency,
        design_score=design_score,
        issues=issues,
        positives=data.get("positives", []),
        summary=data.get("summary", ""),
    )

    critical = sum(1 for i in issues if i.severity == "critical")
    high = sum(1 for i in issues if i.severity == "high")
    print(f"  Frontend critic: {report.design_score}/100 design score | {critical} critical, {high} high issues")
    print(f"  Hierarchy: {hierarchy} | Compliance: {compliance} | Polish: {polish} | Consistency: {consistency}")
    if report.summary:
        print(f"  {report.summary[:150]}")

    return report
