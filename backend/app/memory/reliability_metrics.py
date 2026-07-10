"""
Reliability metrics — the numbers every experiment must move.

Pure computation over existing telemetry (generation_log.jsonl entries,
canary_history.json runs, patterns.json); scripts/failure_report.py renders
the dashboard. The North-Star number is first_try_success_rate: the
fraction of recent generations that succeeded with ZERO fix iterations —
"the user got a working app on the first attempt".
"""
from collections import Counter


def compute_reliability_metrics(
    gen_entries: list[dict],
    canary_runs: list[dict],
    window: int = 30,
) -> dict:
    """Aggregate stage-level success rates and fix-loop effort from the most
    recent `window` generation-log entries and the same window of canary
    results. Rates are None when there is no data for that stage."""
    recent = [e for e in gen_entries if isinstance(e, dict)][-window:]

    succeeded = [e for e in recent if e.get("succeeded")]
    zero_fix = [e for e in recent if e.get("succeeded") and not e.get("fix_count")]
    fix_counts = [e.get("fix_count") or 0 for e in recent]
    scores = [e["final_score"] for e in recent if isinstance(e.get("final_score"), (int, float))]

    # Per-stage pass rates from canary history (build/runtime/crud/browser
    # booleans; None = dimension didn't run, excluded from its rate).
    stage_totals: Counter = Counter()
    stage_passes: Counter = Counter()
    deployed_total = deployed_ok = 0
    for run in canary_runs[-window:]:
        deploy_attempted = bool(run.get("deploy"))
        for r in run.get("results") or []:
            for key, stage in (("build_ok", "build"), ("runtime_ok", "runtime"),
                               ("crud_ok", "crud"), ("browser_ok", "browser")):
                val = r.get(key)
                if val is not None:
                    stage_totals[stage] += 1
                    stage_passes[stage] += bool(val)
            # canaries usually run --no-deploy: deployed=False there means
            # SKIPPED, not failed — only runs that attempted deploy count.
            if deploy_attempted:
                deployed_total += 1
                deployed_ok += bool(r.get("deployed"))

    def _rate(passes, total):
        return round(100.0 * passes / total, 1) if total else None

    # Most common recent failure class, from dominant_errors via the
    # taxonomy classifier (single classification point).
    from app.memory.failure_memory import classify_failure, stage_of
    class_counts: Counter = Counter()
    stage_counts: Counter = Counter()
    for e in recent:
        if e.get("succeeded"):
            continue
        classified = classify_failure(str(e.get("dominant_errors", "")))
        if classified:
            stage, key = classified
            class_counts[key] += 1
            stage_counts[stage] += 1
        else:
            class_counts["Unclassified"] += 1
            stage_counts["unclassified"] += 1

    return {
        "window": len(recent),
        "generation_success_rate": _rate(len(succeeded), len(recent)),
        "first_try_success_rate": _rate(len(zero_fix), len(recent)),
        "avg_fix_iterations": round(sum(fix_counts) / len(fix_counts), 2) if fix_counts else None,
        "avg_forge_score": round(sum(scores) / len(scores), 1) if scores else None,
        "stage_rates": {
            stage: _rate(stage_passes[stage], stage_totals[stage])
            for stage in ("build", "runtime", "crud", "browser")
        },
        "deploy_rate": _rate(deployed_ok, deployed_total),
        "top_failure_classes": class_counts.most_common(5),
        "failure_stage_breakdown": dict(stage_counts.most_common()),
    }


def _bar(rate: float | None, width: int = 24) -> str:
    if rate is None:
        return "(no data)".ljust(width + 6)
    filled = int(round(rate / 100 * width))
    return "█" * filled + "░" * (width - filled) + f"  {rate:5.1f}%"


def render_dashboard(m: dict) -> str:
    """ASCII reliability dashboard — internal, for the optimization loop."""
    lines = [
        "=" * 70,
        f"  FORGEAI RELIABILITY  (last {m['window']} generations)",
        "=" * 70,
        f"  Generation success   {_bar(m['generation_success_rate'])}",
        f"  First-try (0 fixes)  {_bar(m['first_try_success_rate'])}   <- NORTH STAR",
        f"  Build                {_bar(m['stage_rates']['build'])}",
        f"  Runtime              {_bar(m['stage_rates']['runtime'])}",
        f"  CRUD journey         {_bar(m['stage_rates']['crud'])}",
        f"  Browser UX           {_bar(m['stage_rates']['browser'])}",
        f"  Deployment           {_bar(m['deploy_rate'])}",
        "",
        f"  Avg fix iterations   {m['avg_fix_iterations']}",
        f"  Avg forge score      {m['avg_forge_score']}",
    ]
    if m["top_failure_classes"]:
        lines.append("")
        lines.append("  Most common failures:")
        for key, count in m["top_failure_classes"]:
            lines.append(f"    {count:3d}x  {key}")
    if m["failure_stage_breakdown"]:
        stages = "  ".join(f"{s}:{c}" for s, c in m["failure_stage_breakdown"].items())
        lines.append(f"\n  Failures by stage: {stages}")
    return "\n".join(lines)
