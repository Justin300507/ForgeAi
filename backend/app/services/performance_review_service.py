"""
V6 Performance Review Agent

Detects performance anti-patterns in generated FastAPI/SQLAlchemy code:
- N+1 query problems (loops executing per-row queries)
- Missing database indexes on FK/filter columns
- Large payload risks (unbounded list endpoints)
- Slow API warnings (sync ops in handlers, missing pooling)

Outputs a PerformanceReport used in the V6 scoring pipeline.
"""
from dataclasses import dataclass, field
from pathlib import Path
from app.providers.ai_provider import generate_content
from app.prompts.performance_review_prompt import build_performance_review_prompt
from app.utils.json_cleaner import extract_json


@dataclass
class NPlusOneIssue:
    file: str
    line_hint: str
    description: str
    fix: str


@dataclass
class MissingIndexIssue:
    model_file: str
    column: str
    description: str
    fix: str


@dataclass
class LargePayloadIssue:
    endpoint: str
    file: str
    description: str
    fix: str


@dataclass
class SlowAPIIssue:
    file: str
    description: str
    fix: str


@dataclass
class PerformanceReport:
    performance_score: int          # 0-100
    n_plus_one_issues: list[NPlusOneIssue] = field(default_factory=list)
    missing_index_issues: list[MissingIndexIssue] = field(default_factory=list)
    large_payload_issues: list[LargePayloadIssue] = field(default_factory=list)
    slow_api_issues: list[SlowAPIIssue] = field(default_factory=list)
    n_plus_one_count: int = 0
    missing_indexes: int = 0
    large_payload_risks: int = 0
    slow_api_warnings: int = 0
    passed_checks: list[str] = field(default_factory=list)
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


def run_performance_review(
    project_path: str,
    architecture: dict,
    provider: str = "auto",
) -> PerformanceReport:
    """Run the Performance Review agent on a generated project."""
    print("\n=== PERFORMANCE REVIEW (V6) ===")

    files = _load_python_files(project_path)
    if not files:
        return PerformanceReport(
            performance_score=0,
            skipped=True, skip_reason="No Python files found",
        )

    try:
        prompt = build_performance_review_prompt(files, architecture)
        raw_text = generate_content(prompt, provider, max_tokens=3000, stage="performance_review")
        data = extract_json(raw_text)
    except Exception as e:
        print(f"  Performance review failed: {e}")
        return PerformanceReport(
            performance_score=70,
            skipped=True, skip_reason=str(e),
        )

    n1_issues = [
        NPlusOneIssue(
            file=i.get("file", ""),
            line_hint=i.get("line_hint", ""),
            description=i.get("description", ""),
            fix=i.get("fix", ""),
        )
        for i in data.get("n_plus_one_issues", [])
    ]

    idx_issues = [
        MissingIndexIssue(
            model_file=i.get("model_file", ""),
            column=i.get("column", ""),
            description=i.get("description", ""),
            fix=i.get("fix", ""),
        )
        for i in data.get("missing_index_issues", [])
    ]

    payload_issues = [
        LargePayloadIssue(
            endpoint=i.get("endpoint", ""),
            file=i.get("file", ""),
            description=i.get("description", ""),
            fix=i.get("fix", ""),
        )
        for i in data.get("large_payload_issues", [])
    ]

    slow_issues = [
        SlowAPIIssue(
            file=i.get("file", ""),
            description=i.get("description", ""),
            fix=i.get("fix", ""),
        )
        for i in data.get("slow_api_issues", [])
    ]

    report = PerformanceReport(
        performance_score=int(data.get("performance_score", 70)),
        n_plus_one_issues=n1_issues,
        missing_index_issues=idx_issues,
        large_payload_issues=payload_issues,
        slow_api_issues=slow_issues,
        n_plus_one_count=int(data.get("n_plus_one_count", len(n1_issues))),
        missing_indexes=int(data.get("missing_indexes", len(idx_issues))),
        large_payload_risks=int(data.get("large_payload_risks", len(payload_issues))),
        slow_api_warnings=int(data.get("slow_api_warnings", len(slow_issues))),
        passed_checks=data.get("passed_checks", []),
        summary=data.get("summary", ""),
    )

    total_issues = report.n_plus_one_count + report.missing_indexes + report.large_payload_risks + report.slow_api_warnings
    print(f"  Performance score: {report.performance_score}/100 | {total_issues} issues total")
    print(f"  N+1: {report.n_plus_one_count} | Missing indexes: {report.missing_indexes} | "
          f"Large payloads: {report.large_payload_risks} | Slow APIs: {report.slow_api_warnings}")

    for issue in n1_issues[:2]:
        print(f"  [N+1]  {issue.file}: {issue.description[:80]}")
    for issue in idx_issues[:2]:
        print(f"  [IDX]  {issue.model_file}.{issue.column}: {issue.description[:60]}")
    for issue in payload_issues[:2]:
        print(f"  [PAYLOAD] {issue.endpoint}: {issue.description[:60]}")

    return report


def print_performance_report(report: PerformanceReport) -> None:
    if report.skipped:
        return
    print(f"\n{'='*60}")
    print(f"  PERFORMANCE REPORT — Score: {report.performance_score}/100")
    print(f"  {report.summary[:200]}")
    if report.n_plus_one_issues:
        print(f"\n  N+1 Query Issues ({len(report.n_plus_one_issues)}):")
        for i in report.n_plus_one_issues:
            print(f"  {i.file}: {i.description}")
            print(f"    Fix: {i.fix}")
    if report.missing_index_issues:
        print(f"\n  Missing Indexes ({len(report.missing_index_issues)}):")
        for i in report.missing_index_issues:
            print(f"  {i.model_file}.{i.column}: {i.fix}")
    if report.large_payload_issues:
        print(f"\n  Large Payload Risks ({len(report.large_payload_issues)}):")
        for i in report.large_payload_issues:
            print(f"  {i.endpoint}: {i.description}")
    if report.passed_checks:
        print(f"\n  Passed: {', '.join(report.passed_checks[:4])}")
    print("=" * 60)
