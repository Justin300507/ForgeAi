"""
V5.8 Benchmark Leaderboard

Reads all historical benchmark runs from results.json and
benchmark/history/ and produces ranked category-level analysis.
"""
import json
import os
from pathlib import Path
from datetime import datetime

_RESULTS_FILE = Path(__file__).parent / "results.json"
_HISTORY_DIR = Path(__file__).parent / "history"


def load_latest_run() -> dict | None:
    if not _RESULTS_FILE.exists():
        return None
    try:
        data = json.loads(_RESULTS_FILE.read_text(encoding="utf-8"))
        return data
    except Exception:
        return None


def load_all_runs() -> list[dict]:
    runs = []
    # Historical snapshots
    if _HISTORY_DIR.exists():
        for f in sorted(_HISTORY_DIR.glob("*.json")):
            try:
                runs.append(json.loads(f.read_text(encoding="utf-8")))
            except Exception:
                pass
    # Current results
    latest = load_latest_run()
    if latest:
        runs.append(latest)
    return runs


def _score_app(entry: dict) -> int:
    return entry.get("score", 0)


def _is_perfect(entry: dict) -> bool:
    return entry.get("score", 0) == 100


def build_category_report(results: list[dict], categories: dict) -> dict:
    """Build per-category stats from a list of run results."""
    report = {}
    for category, indices in categories.items():
        apps = [results[i] for i in indices if i < len(results)]
        if not apps:
            continue
        scores = [_score_app(a) for a in apps]
        perfect = sum(1 for a in apps if _is_perfect(a))
        report[category] = {
            "count": len(apps),
            "perfect": perfect,
            "perfect_pct": round(perfect / len(apps) * 100, 1) if apps else 0,
            "mean_score": round(sum(scores) / len(scores), 1) if scores else 0,
            "min_score": min(scores) if scores else 0,
            "max_score": max(scores) if scores else 0,
            "failures": [a["idea"][:60] for a in apps if not _is_perfect(a)],
        }
    return report


def print_leaderboard(results: list[dict], categories: dict, label: str = "") -> None:
    report = build_category_report(results, categories)

    total_apps = len(results)
    perfect_total = sum(1 for r in results if _is_perfect(r))
    mean_score = sum(_score_app(r) for r in results) / total_apps if total_apps else 0

    print(f"\n{'=' * 70}")
    print(f"  FORGEAI BENCHMARK LEADERBOARD{' — ' + label if label else ''}")
    print(f"  {total_apps} apps | {perfect_total}/{total_apps} perfect ({perfect_total/total_apps*100:.1f}%) | Mean: {mean_score:.1f}/100")
    print(f"{'=' * 70}")
    print(f"  {'CATEGORY':<28} {'PERFECT':>8} {'MEAN':>6} {'MIN':>5} {'MAX':>5}")
    print(f"  {'-' * 58}")

    # Sort by perfect_pct desc, then mean desc
    sorted_cats = sorted(report.items(), key=lambda x: (-x[1]["perfect_pct"], -x[1]["mean_score"]))
    for cat, stats in sorted_cats:
        bar = "+" * stats["perfect"] + "." * (stats["count"] - stats["perfect"])
        print(
            f"  {cat:<28} {stats['perfect']:>3}/{stats['count']:<4} "
            f"{stats['mean_score']:>6.1f} {stats['min_score']:>5} {stats['max_score']:>5}  [{bar}]"
        )

    print(f"\n  FAILURES:")
    for cat, stats in sorted_cats:
        for fail in stats["failures"]:
            print(f"    [{cat}] {fail}")

    print(f"{'=' * 70}")


def save_run_snapshot(run_data: dict, label: str = "") -> Path:
    """Archive a benchmark run to history/ for longitudinal comparison."""
    _HISTORY_DIR.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    slug = label.replace(" ", "_").lower() if label else "run"
    out = _HISTORY_DIR / f"{ts}_{slug}.json"
    out.write_text(json.dumps(run_data, indent=2, ensure_ascii=False), encoding="utf-8")
    return out


def compare_runs(run_a: dict, run_b: dict, label_a: str = "A", label_b: str = "B") -> None:
    """Print a side-by-side comparison of two runs."""
    results_a = run_a.get("results", [])
    results_b = run_b.get("results", [])
    n = min(len(results_a), len(results_b))

    improved = []
    regressed = []
    unchanged = []
    for i in range(n):
        sa = _score_app(results_a[i])
        sb = _score_app(results_b[i])
        if sb > sa:
            improved.append((results_a[i]["idea"][:50], sa, sb))
        elif sb < sa:
            regressed.append((results_a[i]["idea"][:50], sa, sb))
        else:
            unchanged.append(results_a[i]["idea"][:50])

    print(f"\n=== COMPARISON: {label_a} vs {label_b} ===")
    print(f"  Improved:  {len(improved)}")
    for idea, old, new in improved[:10]:
        print(f"    +{new-old:3d}  {idea}")
    print(f"  Regressed: {len(regressed)}")
    for idea, old, new in regressed[:10]:
        print(f"    -{old-new:3d}  {idea}")
    print(f"  Unchanged: {len(unchanged)}")
    print(f"  Net delta: {len(improved) - len(regressed):+d} apps")
