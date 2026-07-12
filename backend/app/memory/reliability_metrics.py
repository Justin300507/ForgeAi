"""
Reliability metrics — the numbers every experiment must move.

Pure computation over existing telemetry (generation_log.jsonl entries,
canary_history.json runs, patterns.json); scripts/failure_report.py renders
the dashboard. The North-Star number is first_try_success_rate: the
fraction of recent generations that succeeded with ZERO fix iterations —
"the user got a working app on the first attempt".
"""
import json
from collections import Counter
from pathlib import Path

# Deterministic Prevention Rate: rolls up every individual patcher/
# validation-stage's raw count (keys of GenerationRecord.prevention_counts,
# set in pipeline.py) into a small set of buckets a human can scan. Keys
# not listed here fall into "Other" rather than being silently dropped --
# see compute_prevention_rate's own accounting of that bucket.
DETERMINISTIC_PREVENTION_CATEGORIES: dict[str, str] = {
    # Import validation
    "stage.import_closure": "Import validation",
    "_patch_redirect_missing_backend_imports": "Import validation",
    "_patch_missing_pydantic_imports": "Import validation",
    "_patch_dedupe_frontend_imports": "Import validation",
    "_patch_create_missing_service_stubs": "Import validation",
    "_patch_wire_orphan_routers": "Import validation",
    "_patch_wire_orphan_frontend_routes": "Import validation",
    "_patch_main_fk_imports": "Import validation",
    # Symbol validation
    "stage.symbol_closure": "Symbol validation",
    # Schema validator
    "stage.contract_conformance": "Schema validator",
    "stage.schema_db_assertion": "Schema validator",
    "_patch_create_missing_schemas": "Schema validator",
    "_patch_response_schemas_optional": "Schema validator",
    "_patch_schema_nullable_required_mismatch": "Schema validator",
    "_patch_response_schema_id_and_datetimes": "Schema validator",
    "_patch_deduplicate_schemas": "Schema validator",
    "_patch_missing_create_update_fields": "Schema validator",
    "_patch_list_response_model_mismatch": "Schema validator",
    "_patch_orm_type_in_route_schemas": "Schema validator",
    "patch_add_missing_schema_fields": "Schema validator",
    # Entity validator (models/relationships/FKs/routing)
    "_patch_strip_relationships": "Entity validator",
    "_patch_strip_back_populates": "Entity validator",
    "_patch_dangling_foreign_keys": "Entity validator",
    "_patch_deduplicate_models": "Entity validator",
    "_patch_model_aliases": "Entity validator",
    "_patch_relationship_string_aliases": "Entity validator",
    "_patch_router_names": "Entity validator",
    "patch_reorder_shadowed_static_routes": "Entity validator",
    "patch_model_field_mismatches": "Entity validator",
    "patch_add_missing_model_columns": "Entity validator",
    # Syntax validator
    "stage.compile": "Syntax validator",
    "_inline_content_patches": "Syntax validator",
    "_patch_param_order": "Syntax validator",
    # Pydantic patcher
    "_patch_schemas_from_attributes": "Pydantic patcher",
    "_patch_star_dict_extra_fields": "Pydantic patcher",
    "_patch_unsafe_model_hasattr_filter": "Pydantic patcher",
    "_patch_filtered_ctor_kwarg_collision": "Pydantic patcher",
    "_patch_attr_access_mismatches": "Pydantic patcher",
    "_patch_missing_db_refresh": "Pydantic patcher",
    "patch_missing_required_constructor_kwargs": "Pydantic patcher",
    "patch_filter_dict_unpack_constructor_kwargs": "Pydantic patcher",
    "patch_database_py": "Pydantic patcher",
    # Auth/routing patcher
    "_patch_auth_utils": "Auth patcher",
    "_patch_auth_requirements": "Auth patcher",
    "_patch_auth_routes": "Auth patcher",
    "patch_ensure_auth_pages": "Auth patcher",
    "_patch_login_redirect_target": "Auth patcher",
    "_patch_seed_robustness": "Auth patcher",
    # Frontend patcher
    "run_frontend_patches": "Frontend patcher",
}


def compute_prevention_rate(gen_entries: list[dict], window: int = 30) -> dict:
    """
    Aggregate GenerationRecord.prevention_counts across the most recent
    `window` generations into {category: total_count}, plus the raw
    per-mechanism counts and an "Other" bucket for any prevention_counts
    key not yet mapped in DETERMINISTIC_PREVENTION_CATEGORIES (so a newly
    added patcher shows up immediately instead of silently vanishing from
    the dashboard until someone remembers to categorize it).
    """
    recent = [e for e in gen_entries if isinstance(e, dict)][-window:]

    raw: Counter = Counter()
    by_category: Counter = Counter()
    generations_with_any_prevention = 0
    for e in recent:
        counts = e.get("prevention_counts") or {}
        fired = False
        for name, n in counts.items():
            if not n:
                continue
            fired = True
            raw[name] += n
            category = DETERMINISTIC_PREVENTION_CATEGORIES.get(name, "Other")
            by_category[category] += n
        if fired:
            generations_with_any_prevention += 1

    return {
        "window": len(recent),
        "generations_with_prevention": generations_with_any_prevention,
        "by_category": dict(by_category.most_common()),
        "raw_counts": dict(raw.most_common()),
        "total_preventions": sum(by_category.values()),
    }


def render_prevention_dashboard(m: dict) -> str:
    """ASCII 'Deterministic Prevention Rate' section -- how many failures
    were caught/fixed before the app ever reached runtime, broken down by
    mechanism. The goal named alongside this metric: move failures earlier
    in the pipeline, so this number should trend up over time even as
    downstream (runtime/CRUD) failure counts trend down."""
    lines = [
        "=" * 70,
        f"  DETERMINISTIC PREVENTION RATE  (last {m['window']} generations)",
        "=" * 70,
        f"  {m['total_preventions']} failures prevented before runtime "
        f"across {m['generations_with_prevention']}/{m['window']} generations",
        "",
    ]
    if not m["by_category"]:
        lines.append("  (no prevention_counts recorded yet -- pre-dates this metric)")
        return "\n".join(lines)
    for category, count in m["by_category"].items():
        lines.append(f"  {count:4d}x  {category}")
    return "\n".join(lines)


_AUTH_COMPLETENESS_LOG_PATH = (
    Path(__file__).parent.parent.parent / "failure_memory" / "auth_completeness_log.jsonl"
)


def _load_auth_completeness_entries() -> list[dict]:
    """Reads app/repair/auth_completeness.py's own JSONL telemetry log --
    one record per ensure_auth_completeness() call, written at the two
    v6_orchestrator.py call sites Experiment 071 found bypass the normal
    auth-injection safety net. Missing file (pre-dates this metric, or
    no architecture-repair has fired yet this session) is not an error."""
    if not _AUTH_COMPLETENESS_LOG_PATH.exists():
        return []
    entries = []
    try:
        with open(_AUTH_COMPLETENESS_LOG_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return entries


def compute_auth_completeness_metrics(window: int = 30) -> dict:
    """
    Experiment 071: Auth Completeness metric. Tracks how often
    ensure_auth_completeness() found the generated auth surface already
    complete vs. needed deterministic repair vs. couldn't be repaired
    (missing user model, or a shape the template injection doesn't
    cover) -- over the most recent `window` calls, not generations
    (this check only fires when an architecture repair actually ran,
    so its own call count is a subset of total generations).
    """
    entries = _load_auth_completeness_entries()
    recent = entries[-window:]

    counts = Counter(e.get("status", "unknown") for e in recent)
    total = len(recent)

    failed_examples = [
        {"project": e.get("project_name"), "reason": e.get("reason_after")}
        for e in recent if e.get("status") == "failed"
    ][-5:]

    return {
        "window": total,
        "complete": counts.get("complete", 0),
        "repaired": counts.get("repaired", 0),
        "failed": counts.get("failed", 0),
        "repair_success_rate": (
            round(100.0 * (counts.get("complete", 0) + counts.get("repaired", 0)) / total, 1)
            if total else None
        ),
        "recent_failures": failed_examples,
    }


def render_auth_completeness_dashboard(m: dict) -> str:
    """ASCII 'Auth Completeness' section. window=0 means no architecture
    repair has fired since this metric started being recorded -- not the
    same as "always complete," reported as (no data) rather than 100%."""
    lines = [
        "=" * 70,
        f"  AUTH COMPLETENESS  (last {m['window']} architecture-repair checks)",
        "=" * 70,
    ]
    if not m["window"]:
        lines.append("  (no architecture-repair checks recorded yet)")
        return "\n".join(lines)
    lines.append(f"  complete:  {m['complete']}")
    lines.append(f"  repaired:  {m['repaired']}")
    lines.append(f"  failed:    {m['failed']}")
    lines.append(f"  repair success rate: {m['repair_success_rate']}%")
    if m["recent_failures"]:
        lines.append("")
        lines.append("  Recent failures:")
        for f in m["recent_failures"]:
            lines.append(f"    {f['project']}: {f['reason']}")
    return "\n".join(lines)


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


def compute_observatory(gen_entries: list[dict], canary_runs: list[dict], window: int = 30) -> dict:
    """
    The Observatory cockpit's single data source -- pure aggregation over
    reliability_metrics.compute_reliability_metrics + compute_prevention_rate
    + regression_count (GenerationRecord field) + canary_history.json.
    Deliberately NOT a new subsystem: every number here already exists
    somewhere in this module or generation_log.jsonl; this just answers
    "what does the cockpit need, computed together" (trend deltas, a
    now-vs-historically failure comparison, canary health) that no single
    existing function produces on its own.
    """
    recent = [e for e in gen_entries if isinstance(e, dict)]
    current = compute_reliability_metrics(recent, canary_runs, window=window)

    # Trend: current window vs the window immediately before it (not vs
    # all-time -- a slow-moving all-time average would mute a recent swing).
    prev_slice = recent[-(window * 2):-window] if len(recent) > window else []
    first_try_trend = None
    if prev_slice:
        previous = compute_reliability_metrics(prev_slice, [], window=window)
        if (current["first_try_success_rate"] is not None
                and previous["first_try_success_rate"] is not None):
            first_try_trend = round(
                current["first_try_success_rate"] - previous["first_try_success_rate"], 1)

    # Top failure: all-time ("historically") vs last-window ("now") --
    # the divergence itself is the signal (a class that was #1 all-time but
    # isn't in the current window means it's been fixed; a new #1 in the
    # current window that isn't the all-time leader is an emerging problem).
    all_time = compute_reliability_metrics(recent, [], window=max(len(recent), 1))
    top_failure_historically = all_time["top_failure_classes"][0][0] if all_time["top_failure_classes"] else None
    top_failure_now = current["top_failure_classes"][0][0] if current["top_failure_classes"] else None

    prevention = compute_prevention_rate(recent, window=window)
    auth_completeness = compute_auth_completeness_metrics(window=window)

    regression_alerts = sum((e.get("regression_count") or 0) for e in recent[-window:])

    canary_health = "Unknown"
    canary_label = "no canary runs recorded"
    if canary_runs:
        last_run = canary_runs[-1]
        scores = [r.get("forge_score", 0) for r in (last_run.get("results") or [])
                  if not r.get("crashed")]
        crashed = any(r.get("crashed") for r in (last_run.get("results") or []))
        if crashed or (scores and min(scores) < 50):
            canary_health = "Unhealthy"
        elif scores and min(scores) < 70:
            canary_health = "Degraded"
        elif scores:
            canary_health = "Healthy"
        canary_label = f"{last_run.get('label', '?')} @ {last_run.get('timestamp', '?')[:10]}"

    return {
        "window": current["window"],
        "first_try_success_rate": current["first_try_success_rate"],
        "first_try_trend": first_try_trend,
        "first_try_confidence": confidence_from_evidence(current["window"]),
        "top_failure_historically": top_failure_historically,
        "top_failure_now": top_failure_now,
        "prevention_by_category": prevention["by_category"],
        "prevention_total": prevention["total_preventions"],
        "avg_fix_iterations": current["avg_fix_iterations"],
        "regression_alerts": regression_alerts,
        "canary_health": canary_health,
        "canary_label": canary_label,
        "generation_success_rate": current["generation_success_rate"],
        "deploy_rate": current["deploy_rate"],
        "auth_completeness": auth_completeness,
    }


def confidence_from_evidence(n: int) -> str:
    """
    Evidence-size confidence label, not a statistical significance test --
    this project's own variance report puts single-run forge_score stdev
    around 24 points, so small-n numbers deserve an explicit "don't over-
    trust this yet" label rather than being presented with the same
    weight as a well-evidenced one. Used for both the North-Star rate
    (evidence = generations in the window) and experiment attribution
    (evidence = canary app-results in a labeled run, typically 3).
    """
    if n >= 30:
        return "High"
    if n >= 10:
        return "Medium"
    return "Low"


def compute_reliability_timeline(canary_runs: list[dict]) -> list[dict]:
    """
    One point per labeled canary run, chronological, average forge_score
    across that run's (non-crashed) app results -- the closest thing to a
    "first-try success over time" the canary data actually supports
    (canary runs don't carry a fix-iteration count the way generation_log
    entries do, so this is average QUALITY per milestone, not a first-try
    rate; labeled honestly as such by the caller, not conflated with the
    North-Star metric).
    """
    points = []
    for run in canary_runs:
        results = run.get("results") or []
        scores = [r.get("forge_score", 0) for r in results if not r.get("crashed")]
        if not scores:
            continue
        points.append({
            "label": run.get("label", "?"),
            "timestamp": run.get("timestamp", "")[:10],
            "avg_score": round(sum(scores) / len(scores), 1),
            "n": len(scores),
        })
    return points


def compute_experiment_attribution(canary_runs: list[dict], limit: int = 8) -> list[dict]:
    """
    Before/after/delta for each consecutive PAIR of labeled canary runs --
    "did this experiment actually move the number." Returns the most
    recent `limit` comparisons, newest first. confidence_from_evidence()
    scores each on its app-result count (n); the delta magnitude itself is
    NOT a confidence signal here (a huge swing can just as easily mean a
    provider-quota confound as a real fix -- this project's own
    experiments.md repeatedly documents exactly that distinction), so
    confidence stays purely evidence-size-based, same rule as the
    North-Star metric, not dressed up as statistical significance.
    """
    points = compute_reliability_timeline(canary_runs)
    pairs = []
    for i in range(1, len(points)):
        before, after = points[i - 1], points[i]
        delta = round(after["avg_score"] - before["avg_score"], 1)
        pairs.append({
            "label": after["label"],
            "timestamp": after["timestamp"],
            "before": before["avg_score"],
            "after": after["avg_score"],
            "delta": delta,
            "confidence": confidence_from_evidence(min(before["n"], after["n"])),
            "evidence_n": after["n"],
            "direction": "improved" if delta > 0 else ("regressed" if delta < 0 else "flat"),
        })
    return list(reversed(pairs[-limit:]))
