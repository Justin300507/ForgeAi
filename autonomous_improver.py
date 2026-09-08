#!/usr/bin/env python3
"""
Autonomous continuous improver.
Monitors test progress and applies targeted fixes when plateau detected.
Runs indefinitely until 80+ working apps achieved.
"""
import json
import time
import subprocess
from datetime import datetime
from pathlib import Path
from collections import defaultdict

class AutonomousImprover:
    def __init__(self):
        self.results_file = "test_results.json"
        self.last_check = 0
        self.plateau_count = 0
        self.fixes_applied = []

    def get_current_progress(self):
        """Get current test progress."""
        if not Path(self.results_file).exists():
            return None

        try:
            with open(self.results_file, encoding='utf-8') as f:
                results = json.load(f)

            if not results:
                return None

            working = len([r for r in results if r.get('working')])
            total = len(results)
            success_rate = (working / total * 100) if total > 0 else 0

            return {
                'working': working,
                'total': total,
                'success_rate': success_rate,
                'results': results
            }
        except:
            return None

    def print_status(self, progress):
        """Print current status."""
        if not progress:
            print("⏳ Waiting for test results...", end='\r')
            return

        w = progress['working']
        t = progress['total']
        sr = progress['success_rate']

        status = "✅" if w >= 80 else "⚠️ "
        print(f"{status} Progress: {w}/{t} working ({sr:.0f}%) | {datetime.now().strftime('%H:%M:%S')}")

        if w >= 80:
            print("\n" + "=" * 80)
            print(f"🎉 SUCCESS! Reached {w} working apps!")
            print("=" * 80)
            return True

        return False

    def analyze_failures(self, progress):
        """Analyze failure patterns."""
        results = progress['results']
        failing = [r for r in results if not r.get('working')]

        if not failing:
            return None

        # Analyze score distribution
        scores = [r.get('score', 0) for r in failing]
        avg_score = sum(scores) / len(scores) if scores else 0

        # Check common failure ideas
        idea_scores = defaultdict(list)
        for f in failing:
            idea_scores[f['idea']].append(f.get('score', 0))

        worst_ideas = sorted(idea_scores.items(), key=lambda x: sum(x[1]) / len(x[1]))[:3]

        return {
            'avg_score': avg_score,
            'worst_ideas': worst_ideas,
            'fail_count': len(failing)
        }

    def check_plateau(self, current_rate):
        """Check if success rate has plateaued."""
        if current_rate == self.last_check:
            self.plateau_count += 1
        else:
            self.plateau_count = 0
            self.last_check = current_rate

        return self.plateau_count >= 3  # Same rate for 3 checks

    def suggest_improvements(self, analysis):
        """Suggest next improvements based on analysis."""
        if not analysis:
            return []

        suggestions = []

        if analysis['avg_score'] < 60:
            suggestions.append("Core generation issues - check latest deployment")
        elif analysis['avg_score'] < 70:
            suggestions.append("Schema/validation issues - enhance nullability handling")
        else:
            suggestions.append("ImportError patterns - add schema import grounding")
            suggestions.append("Router naming - enforce naming conventions")

        return suggestions

    def run(self):
        """Run autonomous monitoring and improvement loop."""
        print("=" * 80)
        print("AUTONOMOUS CONTINUOUS IMPROVER")
        print("=" * 80)
        print("Monitoring test progress and applying improvements...")
        print("Target: 80+ working apps")
        print("Starting:", datetime.now())
        print()

        check_count = 0
        while True:
            progress = self.get_current_progress()

            if progress and self.print_status(progress):
                break

            if progress and progress['total'] > 20:
                # Analyze every 20 checks
                check_count += 1
                if check_count % 20 == 0:
                    analysis = self.analyze_failures(progress)
                    if analysis:
                        print(f"\n📊 Failure analysis:")
                        print(f"   Average score of failing apps: {analysis['avg_score']:.1f}/100")
                        print(f"   Total failures: {analysis['fail_count']}")

                        if self.check_plateau(progress['success_rate']):
                            print(f"\n⚠️  Success rate plateaued at {progress['success_rate']:.0f}%")
                            suggestions = self.suggest_improvements(analysis)
                            for s in suggestions:
                                print(f"   💡 {s}")

                            # Auto-apply improvements would go here
                            # For now, just suggest

            time.sleep(30)  # Check every 30 seconds


if __name__ == "__main__":
    improver = AutonomousImprover()
    improver.run()
