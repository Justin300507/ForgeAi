#!/usr/bin/env python3
"""
Real-time failure analyzer and fixer.
Monitors test results and suggests/implements fixes for failing patterns.
"""
import json
import os
import time
from pathlib import Path
from collections import defaultdict

class FailureAnalyzer:
    def __init__(self):
        self.fixes_applied = []
        self.failure_patterns = defaultdict(int)

    def analyze_results(self):
        """Analyze test results and identify patterns."""
        results_file = "test_results.json"

        if not os.path.exists(results_file):
            print("⏳ Waiting for test results...")
            return None

        try:
            with open(results_file, encoding='utf-8') as f:
                results = json.load(f)
        except:
            return None

        if not results:
            return None

        # Categorize results
        working = [r for r in results if r.get('working')]
        failing = [r for r in results if not r.get('working')]

        print("\n" + "=" * 80)
        print("FAILURE ANALYSIS")
        print("=" * 80)
        print(f"Total: {len(results)} | Working: {len(working)} | Failing: {len(failing)}")
        print(f"Success Rate: {len(working) / len(results) * 100:.1f}%")

        if len(working) >= 80:
            print("\n✅ SUCCESS! Reached 80+ working apps!")
            return {"status": "success", "working": len(working), "total": len(results)}

        if failing:
            print("\n📊 TOP FAILING IDEAS:")
            idea_counts = defaultdict(int)
            for f in failing:
                idea_counts[f['idea']] += 1

            for idea, count in sorted(idea_counts.items(), key=lambda x: -x[1])[:5]:
                print(f"   {idea}: {count} failures")

        return {
            "working": len(working),
            "failing": len(failing),
            "total": len(results),
            "success_rate": len(working) / len(results) * 100
        }

    def suggest_fixes(self):
        """Suggest fixes based on failure patterns."""
        print("\n💡 SUGGESTED IMPROVEMENTS:")
        print("   1. Check if MissingEndpoint still occurring (fix is deployed)")
        print("   2. Verify ImportError patterns in failing apps")
        print("   3. Check RouterExportMismatch issues")
        print("   4. Review runtime validation gaps")

    def monitor_progress(self):
        """Monitor test progress."""
        print("\n" + "=" * 80)
        print("MONITORING TEST PROGRESS")
        print("=" * 80)

        last_count = 0
        while True:
            try:
                results_file = "test_progress.json"
                if os.path.exists(results_file):
                    with open(results_file, encoding='utf-8') as f:
                        results = json.load(f)

                    working = len([r for r in results if r.get('working')])
                    failing = len([r for r in results if not r.get('working')])
                    total = working + failing

                    if total > last_count:
                        print(f"\n⏱️  {datetime.now().strftime('%H:%M:%S')} - "
                              f"Progress: {total} apps tested | "
                              f"✅ {working} working | "
                              f"❌ {failing} failing | "
                              f"{working/total*100:.0f}% success")
                        last_count = total

                        if working >= 80:
                            print("\n🎉 TARGET REACHED!")
                            return True

            except:
                pass

            time.sleep(10)  # Check every 10 seconds

if __name__ == "__main__":
    from datetime import datetime
    analyzer = FailureAnalyzer()

    # Monitor until complete
    if analyzer.monitor_progress():
        print("\n✅ SUCCESS - 80+ working apps achieved!")
    else:
        print("\n⚠️  Tests may still be running or need manual intervention")
