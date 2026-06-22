"""
V6 Security Reviewer Agent

LLM-based security audit of generated code. Runs after backend generation,
before the validation loop. Finds auth gaps, hardcoded secrets, IDOR, etc.
Outputs SecurityReport that gets shown in the result and affects forge score.
"""
import os
from dataclasses import dataclass, field
from pathlib import Path
from app.providers.ai_provider import generate_content
from app.prompts.security_review_prompt import build_security_review_prompt
from app.utils.json_cleaner import extract_json


@dataclass
class SecurityFinding:
    severity: str       # critical | high | medium | low | info
    category: str
    file: str
    line_hint: str
    description: str
    recommendation: str


@dataclass
class SecurityReport:
    security_score: int     # 0-100
    risk_level: str         # low | medium | high | critical
    findings: list[SecurityFinding] = field(default_factory=list)
    critical_count: int = 0
    high_count: int = 0
    passed_checks: list[str] = field(default_factory=list)
    summary: str = ""
    skipped: bool = False
    skip_reason: str = ""


def _load_project_files(project_path: str, max_files: int = 25) -> dict:
    """Load Python source files from the generated project."""
    files = {}
    base = Path(project_path)
    for py_file in sorted(base.rglob("*.py"))[:max_files]:
        try:
            rel = py_file.relative_to(base)
            content = py_file.read_text(encoding="utf-8", errors="replace")
            files[str(rel).replace("\\", "/")] = content
        except Exception:
            pass
    return files


def run_security_review(project_path: str, provider: str = "auto") -> SecurityReport:
    """
    Run the Security Reviewer agent on a generated project.
    Returns a SecurityReport with findings ranked by severity.
    """
    print("\n=== SECURITY REVIEW (V6) ===")

    files = _load_project_files(project_path)
    if not files:
        return SecurityReport(
            security_score=0, risk_level="unknown",
            skipped=True, skip_reason="No Python files found in project",
        )

    try:
        prompt = build_security_review_prompt(files)
        raw_text = generate_content(prompt, provider, max_tokens=3000, stage="security_review")
        data = extract_json(raw_text)
    except Exception as e:
        print(f"  Security review failed: {e}")
        return SecurityReport(
            security_score=50, risk_level="unknown",
            skipped=True, skip_reason=str(e),
        )

    findings = [
        SecurityFinding(
            severity=f.get("severity", "info"),
            category=f.get("category", "Unknown"),
            file=f.get("file", ""),
            line_hint=f.get("line_hint", ""),
            description=f.get("description", ""),
            recommendation=f.get("recommendation", ""),
        )
        for f in data.get("findings", [])
    ]

    report = SecurityReport(
        security_score=data.get("security_score", 50),
        risk_level=data.get("risk_level", "medium"),
        findings=findings,
        critical_count=data.get("critical_count", sum(1 for f in findings if f.severity == "critical")),
        high_count=data.get("high_count", sum(1 for f in findings if f.severity == "high")),
        passed_checks=data.get("passed_checks", []),
        summary=data.get("summary", ""),
    )

    print(f"  Security score: {report.security_score}/100 | Risk: {report.risk_level}")
    print(f"  Findings: {report.critical_count} critical, {report.high_count} high, {len(findings)} total")
    if report.critical_count > 0:
        for f in findings:
            if f.severity == "critical":
                print(f"  [CRITICAL] {f.file}: {f.description}")
    if report.passed_checks:
        print(f"  Passed: {', '.join(report.passed_checks[:3])}")

    return report


def print_security_report(report: SecurityReport) -> None:
    if report.skipped:
        return
    print(f"\n{'='*60}")
    print(f"  SECURITY REPORT — Score: {report.security_score}/100 [{report.risk_level.upper()}]")
    print(f"  {report.summary}")
    if report.findings:
        print(f"\n  Findings ({len(report.findings)}):")
        for f in sorted(report.findings, key=lambda x: {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}.get(x.severity, 5)):
            print(f"  [{f.severity.upper():8s}] {f.category} in {f.file}")
            print(f"             {f.description}")
            print(f"             Fix: {f.recommendation}")
    if report.passed_checks:
        print(f"\n  Passed: {', '.join(report.passed_checks)}")
    print("=" * 60)
