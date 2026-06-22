"""
V6 Code Review Agent

Reviews generated code for:
- Naming quality (variables, functions, classes, routers)
- Architecture quality (separation of concerns, thin handlers)
- Maintainability (SRP, duplication, magic values)
- Technical debt (TODOs, stubs, hardcoded values)

Outputs a CodeReviewReport used in the V6 scoring pipeline.
"""
from dataclasses import dataclass, field
from pathlib import Path
from app.providers.ai_provider import generate_content
from app.prompts.code_review_prompt import build_code_review_prompt
from app.utils.json_cleaner import extract_json


@dataclass
class CodeIssue:
    severity: str       # critical | high | medium | low
    category: str       # naming | architecture | maintainability | tech_debt
    file: str
    line_hint: str
    description: str
    suggestion: str


@dataclass
class CodeReviewReport:
    naming_score: int           # 0-100
    architecture_score: int     # 0-100
    maintainability_score: int  # 0-100
    tech_debt_score: int        # 0-100 (higher = less debt)
    overall_score: int          # weighted average
    issues: list[CodeIssue] = field(default_factory=list)
    positives: list[str] = field(default_factory=list)
    summary: str = ""
    skipped: bool = False
    skip_reason: str = ""


def _load_python_files(project_path: str, max_files: int = 20) -> dict:
    files = {}
    base = Path(project_path)
    for py_file in sorted(base.rglob("*.py"))[:max_files]:
        try:
            rel = str(py_file.relative_to(base)).replace("\\", "/")
            files[rel] = py_file.read_text(encoding="utf-8", errors="replace")
        except Exception:
            pass
    return files


def run_code_review(
    project_path: str,
    architecture: dict,
    provider: str = "auto",
) -> CodeReviewReport:
    """Run the Code Review agent on a generated project."""
    print("\n=== CODE REVIEW (V6) ===")

    files = _load_python_files(project_path)
    if not files:
        return CodeReviewReport(
            naming_score=0, architecture_score=0,
            maintainability_score=0, tech_debt_score=0, overall_score=0,
            skipped=True, skip_reason="No Python files found",
        )

    try:
        prompt = build_code_review_prompt(files, architecture)
        raw_text = generate_content(prompt, provider, max_tokens=3000, stage="code_review")
        data = extract_json(raw_text)
    except Exception as e:
        print(f"  Code review failed: {e}")
        return CodeReviewReport(
            naming_score=70, architecture_score=70,
            maintainability_score=70, tech_debt_score=70, overall_score=70,
            skipped=True, skip_reason=str(e),
        )

    issues = [
        CodeIssue(
            severity=i.get("severity", "medium"),
            category=i.get("category", "maintainability"),
            file=i.get("file", ""),
            line_hint=i.get("line_hint", ""),
            description=i.get("description", ""),
            suggestion=i.get("suggestion", ""),
        )
        for i in data.get("issues", [])
    ]

    naming = int(data.get("naming_score", 70))
    arch = int(data.get("architecture_score", 70))
    maintain = int(data.get("maintainability_score", 70))
    debt = int(data.get("tech_debt_score", 70))
    overall = int(data.get("overall_score", (naming + arch + maintain + debt) // 4))

    report = CodeReviewReport(
        naming_score=naming,
        architecture_score=arch,
        maintainability_score=maintain,
        tech_debt_score=debt,
        overall_score=overall,
        issues=issues,
        positives=data.get("positives", []),
        summary=data.get("summary", ""),
    )

    critical = sum(1 for i in issues if i.severity == "critical")
    high = sum(1 for i in issues if i.severity == "high")
    print(f"  Code review: {report.overall_score}/100 overall | {critical} critical, {high} high issues")
    print(f"  Naming: {report.naming_score} | Architecture: {report.architecture_score} | "
          f"Maintainability: {report.maintainability_score} | Tech Debt: {report.tech_debt_score}")
    if report.summary:
        print(f"  {report.summary[:120]}")

    return report


def print_code_review_report(report: CodeReviewReport) -> None:
    if report.skipped:
        return
    print(f"\n{'='*60}")
    print(f"  CODE REVIEW REPORT — Overall: {report.overall_score}/100")
    print(f"  Naming: {report.naming_score} | Architecture: {report.architecture_score} | "
          f"Maintainability: {report.maintainability_score} | Tech Debt: {report.tech_debt_score}")
    if report.issues:
        critical_high = [i for i in report.issues if i.severity in ("critical", "high")]
        if critical_high:
            print(f"\n  Critical/High Issues ({len(critical_high)}):")
            for i in critical_high[:5]:
                print(f"  [{i.severity.upper():8s}] [{i.category}] {i.file}")
                print(f"             {i.description}")
                print(f"             Fix: {i.suggestion}")
    if report.positives:
        print(f"\n  Positives: {', '.join(report.positives[:3])}")
    print("=" * 60)
