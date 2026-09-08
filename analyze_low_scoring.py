#!/usr/bin/env python3
"""Analyze low-scoring apps from baseline."""
import json

results = json.load(open('test_results_baseline.json'))
working = [r for r in results if r.get('working')]
low = [r for r in results if r.get('score', 0) > 0 and r.get('score', 0) < 80]
errors = [r for r in results if r.get('error')]

print(f"\n46-APP BASELINE ANALYSIS:")
print(f"Working (>=80): {len(working)}/46")
print(f"Low score (<80): {len(low)}")
print(f"Errors: {len(errors)}\n")

if low:
    print("APPS TO FIX (score <80):")
    for r in sorted(low, key=lambda x: x.get('score', 0)):
        print(f"  App {r['app_num']}: {r['score']:.1f} - {r['idea'][:40]}")

if errors:
    print(f"\nAPPS WITH ERRORS:")
    for r in errors[:5]:
        err = str(r.get('error', 'Unknown'))[:50]
        print(f"  App {r['app_num']}: {err}")
