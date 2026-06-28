"""
ForgeAI KPI Dashboard

Reads benchmark_results/history.json and shows current KPIs vs. targets.
Tracks the six metrics that matter for proving ForgeAI is production-ready.

Usage:
    python -m app.benchmark.kpi_dashboard           # current snapshot
    python -m app.benchmark.kpi_dashboard --trend   # full history table
    python -m app.benchmark.kpi_dashboard --json    # machine-readable output
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from app.benchmark.history import get_history, _HISTORY_PATH

# ── KPI Targets ────────────────────────────────────────────────────────────────
# Format: key → (display_name, target_value, higher_is_better, unit)
TARGETS: dict[str, tuple[str, float, bool, str]] = {
    "weighted_score":    ("ForgeBench Weighted Score",   90.0,  True,  "/100"),
    "runtime_rate":      ("Successful Deployments",       0.95,  True,  "%"),
    "intervention_rate": ("Manual Intervention Rate",     0.05,  False, "%"),
    "cost_per_success":  ("Cost per Working App",         0.05,  False, "$"),
    "avg_time_s":        ("Avg Generation Time",         30.0,  False, "s"),
    "first_pass_rate":   ("First-Pass Success",           0.80,  True,  "%"),
}

_PCT_KEYS = {"runtime_rate", "intervention_rate", "first_pass_rate"}
_DOLLAR_KEYS = {"cost_per_success"}
_SECOND_KEYS = {"avg_time_s"}


def _format(key: str, value: float) -> str:
    if key in _PCT_KEYS:
        return f"{value * 100:.1f}%"
    elif key in _DOLLAR_KEYS:
        return f"${value:.4f}"
    elif key in _SECOND_KEYS:
        return f"{value:.0f}s"
    else:
        return f"{value:.1f}"


def _status(key: str, value: float, target: float, higher_is_better: bool) -> tuple[str, str]:
    """Returns (symbol, label) for this KPI."""
    if higher_is_better:
        gap = value - target
    else:
        gap = target - value

    if gap >= 0:
        return "✓", "ON TARGET"
    elif gap >= -0.10 * abs(target):   # within 10% of target
        return "↗", "CLOSE"
    else:
        return "✗", "NEEDS WORK"


def print_dashboard(json_mode: bool = False) -> None:
    history = get_history()

    if not history:
        print("No benchmark history found. Run ForgeBench-20 first:")
        print("  python run_benchmark.py --difficulty golden")
        return

    latest = history[-1]

    # Enrich with first_pass_rate if present (may not be in older runs)
    if "first_pass_rate" not in latest and "compile_rate" in latest:
        latest["first_pass_rate"] = latest.get("compile_rate", 0)

    if json_mode:
        output = {
            "version": latest.get("version"),
            "run_id": latest.get("run_id"),
            "date": latest.get("date"),
            "kpis": {},
        }
        for key, (name, target, higher, unit) in TARGETS.items():
            value = latest.get(key, 0)
            sym, label = _status(key, value, target, higher)
            output["kpis"][key] = {
                "name": name, "value": value, "target": target,
                "unit": unit, "status": label,
            }
        print(json.dumps(output, indent=2))
        return

    version = latest.get("version", "?")
    date    = latest.get("date", "?")
    run_id  = latest.get("run_id", "?")[:20]
    passed  = latest.get("passed", "?")
    total   = latest.get("total", "?")

    W = 66
    print(f"\n{'═' * W}")
    print(f"  ForgeAI KPI Dashboard — {version}  ({date}  {run_id})")
    print(f"  ForgeBench-20: {passed}/{total} successful deployments")
    print(f"{'═' * W}")
    print(f"  {'KPI':<32} {'TARGET':>8}  {'CURRENT':>8}  STATUS")
    print(f"  {'-' * 62}")

    all_green = True
    for key, (name, target, higher, unit) in TARGETS.items():
        value = float(latest.get(key, 0))
        sym, label = _status(key, value, target, higher)
        if label != "ON TARGET":
            all_green = False

        target_str  = _format(key, target)
        current_str = _format(key, value)

        print(f"  {name:<32} {target_str:>8}  {current_str:>8}  {sym} {label}")

    print(f"{'═' * W}")
    if all_green:
        print(f"  ★ All KPIs on target — ready for beta users")
    else:
        missing = [
            name for key, (name, target, higher, unit) in TARGETS.items()
            if _status(key, float(latest.get(key, 0)), target, higher)[1] == "NEEDS WORK"
        ]
        print(f"  Focus: {', '.join(missing[:2])}")
    print(f"{'═' * W}\n")

    # Trend: show delta from previous run if exists
    if len(history) >= 2:
        prev = history[-2]
        deltas = []
        for key in ("weighted_score", "runtime_rate", "cost_per_success"):
            cur_v = float(latest.get(key, 0))
            pre_v = float(prev.get(key, 0))
            d = cur_v - pre_v
            if abs(d) > 0.001:
                sign = "+" if d > 0 else ""
                deltas.append(f"{key}={sign}{_format(key, d)}")
        if deltas:
            print(f"  vs {prev.get('version','?')}: {', '.join(deltas)}")
            print()


def print_trend() -> None:
    """Print the full version history, then the current KPI dashboard."""
    from app.benchmark.history import print_trend as _pt
    _pt()
    print_dashboard()


def main():
    args = sys.argv[1:]
    if "--trend" in args:
        print_trend()
    elif "--json" in args:
        print_dashboard(json_mode=True)
    else:
        print_dashboard()


if __name__ == "__main__":
    main()
