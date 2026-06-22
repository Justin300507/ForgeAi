"""
V6 Multi-Dimensional Score

Aggregates scores from all V6 agents into a single V6 score:
  - Runtime Success (30%)     — did it start and pass smoke tests?
  - Security Score (25%)      — from SecurityReviewAgent
  - Performance Score (15%)   — from PerformanceReviewAgent
  - Maintainability Score (15%) — from CodeReviewAgent (naming+arch+maintain+debt)
  - Review Score (15%)        — QA coverage + code review overall

Produces a grade (S/A/B/C/D/F) with per-dimension breakdown.
"""


def calculate_v6_score(
    forge_score: dict,
    security_report,
    performance_report,
    code_review_report,
    qa_report,
    runtime_result: dict | None,
) -> dict:
    """
    Returns:
        {
          "total": 0-100,
          "grade": "S|A|B|C|D|F",
          "dimensions": {
            "runtime": 0-100,
            "security": 0-100,
            "performance": 0-100,
            "maintainability": 0-100,
            "review": 0-100,
          },
          "breakdown": {...detail...}
        }
    """
    # ── Runtime dimension ────────────────────────────────────────────────
    runtime_passed = bool(runtime_result and runtime_result.get("success"))
    static_passed = forge_score.get("score", 0) >= 60
    if runtime_passed and static_passed:
        runtime_dim = 100
    elif runtime_passed:
        runtime_dim = 75
    elif static_passed:
        runtime_dim = 40
    else:
        runtime_dim = max(0, forge_score.get("score", 0) - 20)

    # ── Security dimension ───────────────────────────────────────────────
    if not security_report or security_report.skipped:
        security_dim = 70  # neutral if skipped
    else:
        security_dim = max(0, min(100, security_report.security_score))
        # Extra penalty: critical findings drag the score down further
        security_dim -= security_report.critical_count * 15
        security_dim = max(0, security_dim)

    # ── Performance dimension ────────────────────────────────────────────
    if not performance_report or performance_report.skipped:
        performance_dim = 70
    else:
        performance_dim = max(0, min(100, performance_report.performance_score))
        # N+1 queries are the worst perf issue
        performance_dim -= performance_report.n_plus_one_count * 10
        performance_dim -= performance_report.missing_indexes * 3
        performance_dim -= performance_report.large_payload_risks * 5
        performance_dim = max(0, performance_dim)

    # ── Maintainability dimension ────────────────────────────────────────
    if not code_review_report or code_review_report.skipped:
        maintain_dim = 70
    else:
        maintain_dim = (
            code_review_report.naming_score * 0.25
            + code_review_report.architecture_score * 0.30
            + code_review_report.maintainability_score * 0.25
            + code_review_report.tech_debt_score * 0.20
        )
        maintain_dim = max(0, min(100, int(maintain_dim)))

    # ── Review dimension (QA + Code Review) ─────────────────────────────
    if not qa_report or qa_report.skipped:
        qa_score = 70
    else:
        qa_score = max(0, min(100, qa_report.qa_score))

    code_overall = (
        max(0, min(100, code_review_report.overall_score))
        if code_review_report and not code_review_report.skipped
        else 70
    )
    review_dim = int(qa_score * 0.5 + code_overall * 0.5)

    # ── Weighted total ───────────────────────────────────────────────────
    weights = {
        "runtime": 0.30,
        "security": 0.25,
        "performance": 0.15,
        "maintainability": 0.15,
        "review": 0.15,
    }
    total = (
        runtime_dim * weights["runtime"]
        + security_dim * weights["security"]
        + performance_dim * weights["performance"]
        + maintain_dim * weights["maintainability"]
        + review_dim * weights["review"]
    )
    total = max(0, min(100, int(total)))

    grade = (
        "S" if total >= 95 else
        "A" if total >= 85 else
        "B" if total >= 75 else
        "C" if total >= 65 else
        "D" if total >= 50 else
        "F"
    )

    return {
        "total": total,
        "grade": grade,
        "dimensions": {
            "runtime": runtime_dim,
            "security": security_dim,
            "performance": performance_dim,
            "maintainability": maintain_dim,
            "review": review_dim,
        },
        "breakdown": {
            "forge_score": forge_score.get("score", 0),
            "runtime_passed": runtime_passed,
            "static_passed": static_passed,
            "security_score": getattr(security_report, "security_score", None),
            "security_critical": getattr(security_report, "critical_count", 0),
            "performance_score": getattr(performance_report, "performance_score", None),
            "n_plus_one_count": getattr(performance_report, "n_plus_one_count", 0),
            "naming_score": getattr(code_review_report, "naming_score", None),
            "architecture_score": getattr(code_review_report, "architecture_score", None),
            "maintainability_score": getattr(code_review_report, "maintainability_score", None),
            "tech_debt_score": getattr(code_review_report, "tech_debt_score", None),
            "qa_score": getattr(qa_report, "qa_score", None),
            "qa_blockers": len(getattr(qa_report, "blockers", [])),
        },
        "weights": weights,
    }
