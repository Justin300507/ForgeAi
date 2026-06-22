"""
V7 Autonomous Research Agent

Analyzes benchmark trends across V5/V6/V7 runs, discovers recurring
failure clusters, detects regressions, and produces actionable
improvement recommendations with testable hypotheses.

This agent runs after each benchmark cycle and writes its findings
to failure_memory/research_findings.json for the improvement loop
to act on.
"""
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

_FINDINGS_PATH = Path(__file__).parent.parent.parent / "failure_memory" / "research_findings.json"


@dataclass
class FailureCluster:
    name: str
    patterns: list[str]
    frequency: float
    insight: str


@dataclass
class ImprovementSuggestion:
    rank: int
    improvement: str
    expected_impact: str
    effort: str      # low | medium | high
    type: str        # validator | prompt_rule | architecture | pipeline


@dataclass
class Hypothesis:
    statement: str
    test_method: str
    expected_delta: str


@dataclass
class ResearchFindings:
    trend: str                      # improving | stable | declining
    trend_explanation: str
    regression_detected: bool
    regression_details: str
    root_causes: list[dict]
    cost_efficiency_trend: str
    pattern_clusters: list[FailureCluster]
    top_improvements: list[ImprovementSuggestion]
    hypothesis: Hypothesis | None
    summary: str
    timestamp: str
    skipped: bool = False
    skip_reason: str = ""


def run_research_agent(
    provider: str = "auto",
) -> ResearchFindings:
    """
    Run the Autonomous Research Agent.
    Analyzes all available data and produces findings + recommendations.
    """
    print("\n=== AUTONOMOUS RESEARCH AGENT (V7) ===")
    t0 = time.time()

    from app.providers.ai_provider import generate_content
    from app.prompts.research_agent_prompt import build_research_agent_prompt
    from app.utils.json_cleaner import extract_json
    from app.memory.failure_memory import get_top_patterns, _load
    from app.services.failure_dataset_service import get_index, get_dataset_stats
    from app.services.benchmark_comparison_service import get_comparison_for_research
    from app.services.prompt_optimizer_service import list_accepted_improvements

    # Gather all data
    patterns = get_top_patterns(min_count=1, max_patterns=20)
    dataset_stats = get_dataset_stats()
    v_comparison = get_comparison_for_research()
    accepted_improvements = list_accepted_improvements()

    # Build benchmark history from dataset index
    index_entries = get_index(limit=30)
    benchmark_history = [
        {
            "date": e.get("timestamp", ""),
            "score": e.get("score", 0),
            "pass_rate": 1.0 if e.get("runtime_passed") else 0.0,
            "runtime_rate": 1.0 if e.get("runtime_passed") else 0.0,
            "cost_usd": 0.0,
            "version": e.get("version", "v5"),
        }
        for e in index_entries
    ]

    if not patterns and not index_entries:
        return ResearchFindings(
            trend="unknown", trend_explanation="No data available yet",
            regression_detected=False, regression_details="",
            root_causes=[], cost_efficiency_trend="unknown",
            pattern_clusters=[], top_improvements=[],
            hypothesis=None, summary="No benchmark data available — run more projects first.",
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
            skipped=True, skip_reason="No failure patterns or dataset entries",
        )

    print(f"  Analyzing {len(index_entries)} runs, {len(patterns)} patterns, {len(v_comparison)} versions...")

    try:
        prompt = build_research_agent_prompt(
            benchmark_history=benchmark_history,
            failure_patterns=patterns,
            accepted_improvements=accepted_improvements,
            v_comparison=v_comparison,
        )
        raw = generate_content(prompt, provider, max_tokens=3000, stage="research_agent")
        data = extract_json(raw)
    except Exception as e:
        print(f"  Research agent LLM call failed: {e}")
        return ResearchFindings(
            trend="unknown", trend_explanation="LLM call failed",
            regression_detected=False, regression_details="",
            root_causes=[], cost_efficiency_trend="unknown",
            pattern_clusters=[], top_improvements=[],
            hypothesis=None, summary=str(e),
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
            skipped=True, skip_reason=str(e),
        )

    clusters = [
        FailureCluster(
            name=c.get("cluster_name", ""),
            patterns=c.get("patterns", []),
            frequency=float(c.get("frequency", 0)),
            insight=c.get("insight", ""),
        )
        for c in data.get("pattern_clusters", [])
    ]

    improvements = [
        ImprovementSuggestion(
            rank=i.get("rank", idx + 1),
            improvement=i.get("improvement", ""),
            expected_impact=i.get("expected_impact", ""),
            effort=i.get("effort", "medium"),
            type=i.get("type", "prompt_rule"),
        )
        for idx, i in enumerate(data.get("top_improvements", []))
    ]

    h_data = data.get("hypothesis", {})
    hypothesis = Hypothesis(
        statement=h_data.get("statement", ""),
        test_method=h_data.get("test_method", ""),
        expected_delta=h_data.get("expected_delta", ""),
    ) if h_data else None

    findings = ResearchFindings(
        trend=data.get("trend", "unknown"),
        trend_explanation=data.get("trend_explanation", ""),
        regression_detected=data.get("regression_detected", False),
        regression_details=data.get("regression_details", ""),
        root_causes=data.get("root_causes", []),
        cost_efficiency_trend=data.get("cost_efficiency_trend", "unknown"),
        pattern_clusters=clusters,
        top_improvements=improvements,
        hypothesis=hypothesis,
        summary=data.get("summary", ""),
        timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
    )

    # Persist findings
    _save_findings(findings)

    def _safe(s: str, n: int = 120) -> str:
        return s[:n].encode("ascii", errors="replace").decode("ascii")

    print(f"  Trend: {findings.trend} -- {_safe(findings.trend_explanation, 100)}")
    if findings.regression_detected:
        print(f"  REGRESSION: {_safe(findings.regression_details, 100)}")
    print(f"  Clusters: {len(findings.pattern_clusters)} | Suggestions: {len(findings.top_improvements)}")
    for imp in findings.top_improvements[:3]:
        print(f"  [{imp.rank}] {_safe(imp.improvement, 80)} ({_safe(imp.expected_impact, 40)})")
    if findings.hypothesis:
        print(f"  Hypothesis: {_safe(findings.hypothesis.statement, 120)}")

    return findings


def _save_findings(findings: ResearchFindings) -> None:
    history = []
    if _FINDINGS_PATH.exists():
        try:
            history = json.loads(_FINDINGS_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass

    history.append({
        "timestamp": findings.timestamp,
        "trend": findings.trend,
        "trend_explanation": findings.trend_explanation,
        "regression_detected": findings.regression_detected,
        "regression_details": findings.regression_details,
        "root_causes": findings.root_causes,
        "pattern_clusters": [
            {"name": c.name, "patterns": c.patterns, "frequency": c.frequency, "insight": c.insight}
            for c in findings.pattern_clusters
        ],
        "top_improvements": [
            {"rank": i.rank, "improvement": i.improvement, "expected_impact": i.expected_impact,
             "effort": i.effort, "type": i.type}
            for i in findings.top_improvements
        ],
        "hypothesis": {
            "statement": findings.hypothesis.statement,
            "test_method": findings.hypothesis.test_method,
            "expected_delta": findings.hypothesis.expected_delta,
        } if findings.hypothesis else None,
        "summary": findings.summary,
    })

    _FINDINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _FINDINGS_PATH.write_text(
        json.dumps(history[-50:], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def get_latest_findings() -> dict | None:
    """Return the most recent research findings."""
    if not _FINDINGS_PATH.exists():
        return None
    try:
        history = json.loads(_FINDINGS_PATH.read_text(encoding="utf-8"))
        return history[-1] if history else None
    except Exception:
        return None
