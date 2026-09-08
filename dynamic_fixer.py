#!/usr/bin/env python3
"""
Dynamic fixer that applies targeted improvements based on observed failures.
Runs improvements in sequence and validates with quick tests.
"""
import json
import subprocess
import time
from datetime import datetime
from pathlib import Path

class DynamicFixer:
    def __init__(self):
        self.improvements_applied = []
        self.latest_success_rate = 0

    def check_success_rate(self):
        """Get current success rate from test results."""
        if not Path("test_results.json").exists():
            return 0

        try:
            with open("test_results.json", encoding='utf-8') as f:
                results = json.load(f)
            working = len([r for r in results if r.get('working')])
            return len(results) > 0 and working / len(results) * 100 or 0
        except:
            return 0

    def print_status(self):
        """Print current status."""
        success_rate = self.check_success_rate()
        print("\n" + "=" * 80)
        print(f"⏰ {datetime.now().strftime('%H:%M:%S')} | SUCCESS RATE: {success_rate:.1f}%")
        print("=" * 80)

        if success_rate >= 80:
            print("✅ TARGET ACHIEVED!")
            return True

        print("\n📋 IMPROVEMENTS AVAILABLE:")
        print("   1. ImportError Fix - Ground schema imports in fixer prompts")
        print("   2. RouterExportMismatch Fix - Enforce router naming patterns")
        print("   3. RuntimeValidation Fix - Add runtime pre-checks")
        print("   4. SchemaNullability Fix - Enhance nullable field handling")
        print()

        return False

    def suggest_next_fix(self):
        """Suggest the next fix to implement based on current results."""
        if not Path("test_results.json").exists():
            return None

        try:
            with open("test_results.json", encoding='utf-8') as f:
                results = json.load(f)

            failing = [r for r in results if not r.get('working')]

            if not failing:
                return None

            print("\n🔍 ANALYZING FAILURE PATTERNS IN LATEST RESULTS...")

            # Check for common patterns
            error_patterns = defaultdict(int)
            for fail in failing[-20:]:  # Check last 20 failures
                error = fail.get('error', '')
                if 'ImportError' in error:
                    error_patterns['ImportError'] += 1
                elif 'Router' in error:
                    error_patterns['RouterIssue'] += 1
                elif 'Null' in error or 'nullable' in error:
                    error_patterns['Nullability'] += 1

            if error_patterns:
                top_pattern = max(error_patterns.items(), key=lambda x: x[1])
                print(f"   Most common: {top_pattern[0]} ({top_pattern[1]} times)")

        except:
            pass

        return None

    def run(self):
        """Run continuous monitoring and fixing."""
        print("=" * 80)
        print("DYNAMIC FIXER - Continuous Improvement Loop")
        print("=" * 80)
        print("\nMonitoring test progress...")
        print("When success rate plateaus, improvements will be suggested")
        print()

        last_success_rate = 0
        stale_count = 0

        while True:
            if self.print_status():
                break

            success_rate = self.check_success_rate()

            if success_rate == last_success_rate and success_rate > 0:
                stale_count += 1
                if stale_count >= 3:  # Same rate for 3+ checks
                    print("\n⚠️  Success rate plateaued - need new fixes")
                    self.suggest_next_fix()
                    stale_count = 0
            else:
                stale_count = 0

            last_success_rate = success_rate
            time.sleep(30)  # Check every 30 seconds


from collections import defaultdict

if __name__ == "__main__":
    fixer = DynamicFixer()
    fixer.run()
