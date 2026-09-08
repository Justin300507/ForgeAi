#!/usr/bin/env python3
"""Compare latest canary results against the baseline (2026-07-06)."""
import json
from datetime import datetime

# Load canary history
with open('backend/benchmark_results/canary_history.json', encoding='utf-8') as f:
    data = json.load(f)

# Baseline from 2026-07-06 (before architecture repair enhancement)
baseline = {
    'todo': 76.9,
    'blog_cms': 33.0,
    'crm': 76.9,
}

# Get latest run
latest = data['runs'][-1] if data['runs'] else None

if not latest:
    print("No canary results found")
    exit(1)

print("=" * 80)
print("CANARY VALIDATION RESULTS")
print("=" * 80)
print(f"\nTimestamp: {latest.get('timestamp', 'N/A')}")
print(f"Provider: openai")
print()

total_improvement = 0
apps_improved = 0

for result in latest.get('results', []):
    app = result['app']
    score = result['forge_score']
    baseline_score = baseline.get(app, 0)
    improvement = score - baseline_score
    total_improvement += improvement
    if improvement > 0:
        apps_improved += 1

    status = "✅ IMPROVED" if improvement > 0 else "⚠️  REGRESSED" if improvement < 0 else "→ STABLE"

    print(f"{app.upper()}")
    print(f"  Baseline:       {baseline_score:6.1f}/100")
    print(f"  New Score:      {score:6.1f}/100")
    print(f"  Change:         {improvement:+6.1f}  {status}")
    print(f"  Build OK:       {result.get('build_ok', 'N/A')}")
    print(f"  Runtime OK:     {result.get('runtime_ok', 'N/A')}")
    print(f"  Fix attempts:   {result.get('fix_attempts', 'N/A')}")
    print()

print("=" * 80)
print("SUMMARY")
print("=" * 80)
avg_improvement = total_improvement / len(latest.get('results', [1]))
print(f"Average improvement:     {avg_improvement:+.1f} points")
print(f"Apps improved:           {apps_improved}/{len(latest.get('results', []))}")
print(f"Total improvement:       {total_improvement:+.1f} points")
print()

if avg_improvement > 0:
    print("✅ FIX VALIDATED - Architecture repair enhancement is working!")
elif avg_improvement == 0:
    print("⚠️  NO CHANGE - Need to investigate if fix is being applied")
else:
    print("❌ REGRESSION - Fix may have introduced issues")
