#!/usr/bin/env python3
"""
Autonomous deployment and testing runner.
Deploys latest code to Railway and runs continuous 100-app test.
"""
import subprocess
import requests
import json
import time
from datetime import datetime
from pathlib import Path

class DeploymentTester:
    def __init__(self, load_existing=True):
        self.prod_url = "http://127.0.0.1:8000"
        self.results = []
        self.working_count = 0
        self.start_time = datetime.now()

        if load_existing:
            self.load_existing_results()

    def verify_production(self):
        """Verify production server is live."""
        print("=" * 80)
        print("VERIFYING PRODUCTION DEPLOYMENT")
        print("=" * 80)
        print(f"Production URL: {self.prod_url}")

        try:
            response = requests.get(f"{self.prod_url}/health", timeout=10)
            if response.status_code == 200:
                print("✅ Production server is LIVE")
                return True
        except Exception as e:
            print(f"⚠️  Production server check: {e}")

        return False

    def test_app(self, idea, app_num):
        """Generate and test a single app on production."""
        try:
            payload = {"idea": idea, "deploy_to": "none"}
            response = requests.post(
                f"{self.prod_url}/project/v15",
                json=payload,
                timeout=300
            )

            if response.status_code != 200:
                self.log_failure(app_num, idea, f"HTTP {response.status_code}")
                return

            result = response.json()
            gen_data = result.get("generation", {})
            score = gen_data.get("forge_score", {}).get("score", 0)
            grade = gen_data.get("forge_score", {}).get("grade", "F")

            is_working = score >= 80

            elapsed = (datetime.now() - self.start_time).total_seconds()
            elapsed_min = elapsed / 60

            status = "✅" if is_working else "❌"
            print(f"[{app_num:3d}] {status} {score:5.1f}/100 ({grade}) | {idea[:40]:40} | {elapsed_min:5.1f}m")

            if is_working:
                self.working_count += 1

            self.results.append({
                "app_num": app_num,
                "idea": idea,
                "score": score,
                "grade": grade,
                "working": is_working,
                "timestamp": datetime.now().isoformat()
            })

            # Save progress after each app
            self.save_progress()

            # Check if we've reached goal
            total = len(self.results)
            if self.working_count >= 80:
                print("\n" + "=" * 80)
                print(f"🎉 SUCCESS! Reached {self.working_count} working apps out of {total}!")
                print("=" * 80)
                return True

        except Exception as e:
            self.log_failure(app_num, idea, str(e))

        return False

    def log_failure(self, app_num, idea, reason):
        """Log a test failure."""
        elapsed = (datetime.now() - self.start_time).total_seconds() / 60
        print(f"[{app_num:3d}] ❌ FAILED ({reason}) | {idea[:40]:40} | {elapsed:5.1f}m")
        self.results.append({
            "app_num": app_num,
            "idea": idea,
            "score": 0,
            "grade": "F",
            "working": False,
            "error": reason,
            "timestamp": datetime.now().isoformat()
        })
        self.save_progress()

    def save_progress(self):
        """Save results to file."""
        with open("test_results.json", "w", encoding="utf-8") as f:
            json.dump(self.results, f, indent=2)

    def run_100_apps(self):
        """Run 100 app tests."""
        test_ideas = [
            "A todo app with user login",
            "A simple note-taking app",
            "A task management system",
            "A blog with comments",
            "A contact manager",
            "A habit tracker",
            "A fitness log",
            "A recipe collection app",
            "A movie watchlist",
            "An expense tracker",
        ]

        print("\n" + "=" * 80)
        print("STARTING 100-APP PRODUCTION TEST")
        print("=" * 80)
        print(f"Target: 80+ apps with Forge Score ≥80")
        print(f"Start time: {self.start_time}")
        print()

        for i in range(1, 101):
            idea = test_ideas[(i - 1) % len(test_ideas)]

            if self.test_app(idea, i):
                break

            # Check every 10 apps if we should continue
            if i % 10 == 0:
                total = len(self.results)
                working_pct = (self.working_count / total * 100) if total > 0 else 0
                print(f"\n📊 Progress: {self.working_count}/{total} working ({working_pct:.0f}%)\n")

        self.print_final_summary()

    def print_final_summary(self):
        """Print final test summary."""
        print("\n" + "=" * 80)
        print("FINAL TEST SUMMARY")
        print("=" * 80)
        total = len(self.results)
        working_pct = (self.working_count / total * 100) if total > 0 else 0
        elapsed = (datetime.now() - self.start_time).total_seconds() / 60

        print(f"Total apps tested:     {total}")
        print(f"Working apps (80+):    {self.working_count}")
        print(f"Failing apps (<80):    {total - self.working_count}")
        print(f"Success rate:          {working_pct:.1f}%")
        print(f"Total time:            {elapsed:.1f} minutes")
        print()

        if self.working_count >= 80:
            print("🎉 TARGET ACHIEVED! 80+ WORKING APPS!")
        else:
            print(f"⚠️  Need {80 - self.working_count} more working apps to reach target")

            # Analyze failures
            failing = [r for r in self.results if not r.get('working')]
            if failing:
                avg_score = sum(r.get('score', 0) for r in failing) / len(failing)
                print(f"\nAverage score of failing apps: {avg_score:.1f}/100")
                print("Top ideas to improve:")
                idea_counts = {}
                for f in failing:
                    idea = f['idea']
                    idea_counts[idea] = idea_counts.get(idea, 0) + 1
                for idea, count in sorted(idea_counts.items(), key=lambda x: -x[1])[:5]:
                    print(f"  - {idea}: {count} failures")


if __name__ == "__main__":
    tester = DeploymentTester()

    # Verify production is live
    if not tester.verify_production():
        print("❌ Production server not responding. Exiting.")
        exit(1)

    print()

    # Run 100 app tests
    tester.run_100_apps()
