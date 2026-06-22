"""
V6 QA Team Agent

Reviews the generated application against the PM spec's user stories.
Validates feature coverage, finds missing endpoints, and catches
quality issues the static validators miss.
"""
from dataclasses import dataclass, field
from pathlib import Path
from app.providers.ai_provider import generate_content
from app.prompts.qa_review_prompt import build_qa_review_prompt
from app.utils.json_cleaner import extract_json


@dataclass
class StoryCoverage:
    story: str
    covered: bool
    endpoint: str
    gap: str


@dataclass
class QAIssue:
    file: str
    issue: str
    fix: str


@dataclass
class QAReport:
    qa_score: int           # 0-100
    user_story_coverage: list[StoryCoverage] = field(default_factory=list)
    missing_features: list[str] = field(default_factory=list)
    code_quality_issues: list[QAIssue] = field(default_factory=list)
    passed: list[str] = field(default_factory=list)
    ready_to_ship: bool = False
    blockers: list[str] = field(default_factory=list)
    skipped: bool = False
    skip_reason: str = ""


def _load_project_files(project_path: str) -> dict:
    files = {}
    base = Path(project_path)
    for py_file in sorted(base.rglob("*.py"))[:20]:
        try:
            rel = str(py_file.relative_to(base)).replace("\\", "/")
            files[rel] = py_file.read_text(encoding="utf-8", errors="replace")
        except Exception:
            pass
    return files


def run_qa_review(
    project_path: str,
    product_spec: dict,
    architecture: dict,
    provider: str = "auto",
) -> QAReport:
    """
    Run QA review against product spec user stories.
    """
    print("\n=== QA REVIEW (V6) ===")

    if not product_spec.get("user_stories"):
        return QAReport(
            qa_score=50, skipped=True,
            skip_reason="No user stories in product spec — run Product Manager first",
        )

    files = _load_project_files(project_path)
    if not files:
        return QAReport(qa_score=0, skipped=True, skip_reason="No files found in project")

    try:
        prompt = build_qa_review_prompt(product_spec, architecture, files)
        raw_text = generate_content(prompt, provider, max_tokens=3000, stage="qa_review")
        data = extract_json(raw_text)
    except Exception as e:
        print(f"  QA review failed: {e}")
        return QAReport(qa_score=50, skipped=True, skip_reason=str(e))

    coverage = [
        StoryCoverage(
            story=s.get("story", ""),
            covered=s.get("covered", False),
            endpoint=s.get("endpoint") or "",
            gap=s.get("gap") or "",
        )
        for s in data.get("user_story_coverage", [])
    ]

    issues = [
        QAIssue(
            file=i.get("file", ""),
            issue=i.get("issue", ""),
            fix=i.get("fix", ""),
        )
        for i in data.get("code_quality_issues", [])
    ]

    report = QAReport(
        qa_score=data.get("qa_score", 50),
        user_story_coverage=coverage,
        missing_features=data.get("missing_features", []),
        code_quality_issues=issues,
        passed=data.get("passed", []),
        ready_to_ship=data.get("ready_to_ship", False),
        blockers=data.get("blockers", []),
    )

    covered = sum(1 for s in coverage if s.covered)
    total = len(coverage)
    print(f"  QA score: {report.qa_score}/100 | Stories: {covered}/{total} covered")
    if report.blockers:
        print(f"  Blockers: {', '.join(report.blockers[:3])}")
    if report.missing_features:
        for mf in report.missing_features[:3]:
            print(f"  [MISSING] {mf}")

    return report
