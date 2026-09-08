#!/usr/bin/env python3
"""
Analyze 100-app test results and identify fixes for apps < 60 score.
"""
import json
import os
from collections import defaultdict

def analyze_failures():
    """Load results and analyze failures."""
    if not os.path.exists('test_results.json'):
        print("❌ No test_results.json found")
        return

    with open('test_results.json') as f:
        results = json.load(f)

    working = [r for r in results if r.get('working')]
    failed = [r for r in results if not r.get('working')]
    low_score = [r for r in results if not r.get('working') and r.get('score', 0) > 0 and r.get('score', 0) < 60]
    errors = [r for r in results if r.get('error')]

    print(f"\n{'='*80}")
    print("100-APP ANALYSIS & FIX STRATEGY")
    print(f"{'='*80}\n")

    print(f"📊 METRICS:")
    print(f"  Working (≥80): {len(working)}/{len(results)} ({len(working)/len(results)*100:.1f}%)")
    print(f"  Low score (1-60): {len(low_score)}")
    print(f"  Connection errors: {len([e for e in errors if 'Connection' in str(e.get('error', ''))])}")
    print(f"  HTTP errors: {len([e for e in errors if 'HTTP' in str(e.get('error', ''))])}")

    if low_score:
        print(f"\n❌ APPS TO FIX (Score 1-60):")
        for r in sorted(low_score, key=lambda x: x.get('score', 0)):
            print(f"  App {r['app_num']}: {r['score']:.1f}/100 - {r['idea'][:45]}")

    # Analyze error patterns
    error_patterns = defaultdict(list)
    for r in errors:
        err = r.get('error', 'Unknown')
        if isinstance(err, str):
            # Categorize error
            if 'Connection' in err or 'Max retries' in err:
                error_patterns['connection'].append(r['app_num'])
            elif 'HTTP' in err:
                error_patterns['http'].append(r['app_num'])
            else:
                error_patterns['other'].append(r['app_num'])

    if error_patterns:
        print(f"\n⚠️  ERROR PATTERNS:")
        for pattern, apps in sorted(error_patterns.items(), key=lambda x: -len(x[1])):
            print(f"  {pattern}: {len(apps)} apps (samples: {apps[:3]}...)")

    print(f"\n{'='*80}\n")
    return results

if __name__ == "__main__":
    analyze_failures()
