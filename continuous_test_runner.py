#!/usr/bin/env python3
"""
Continuous app generation tester.
Generate 100 apps and track which ones work end-to-end (80+ score).
"""
import requests
import json
import time
from datetime import datetime
from pathlib import Path

# Test ideas to cycle through
TEST_IDEAS = [
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

class AppTester:
    def __init__(self):
        self.results = []
        self.working_count = 0
        self.failing_count = 0
        self.server_url = "http://localhost:8000"

    def test_app(self, idea, app_num):
        """Generate and test a single app."""
        print(f"\n[{app_num}/100] Testing: {idea}")
        print("=" * 80)

        try:
            # Generate app
            payload = {"idea": idea, "deploy_to": "none"}
            response = requests.post(
                f"{self.server_url}/project/v15",
                json=payload,
                timeout=300
            )

            if response.status_code != 200:
                self.log_failure(app_num, idea, f"Generation failed: {response.status_code}")
                return

            result = response.json()
            gen_data = result.get("generation", {})
            score = gen_data.get("forge_score", {}).get("score", 0)
            grade = gen_data.get("forge_score", {}).get("grade", "F")

            # Check if it's working (80+ score)
            is_working = score >= 80

            print(f"  Forge Score: {score:.1f}/100 ({grade})")
            print(f"  Status: {'✅ WORKING' if is_working else '❌ FAILING'}")

            if is_working:
                self.working_count += 1
                print(f"  ✅ Progress: {self.working_count} working")
            else:
                self.failing_count += 1
                print(f"  ❌ Progress: {self.failing_count} failing")

            self.results.append({
                "app_num": app_num,
                "idea": idea,
                "score": score,
                "grade": grade,
                "working": is_working,
                "timestamp": datetime.now().isoformat()
            })

            # Show running totals
            total = self.working_count + self.failing_count
            working_pct = (self.working_count / total * 100) if total > 0 else 0
            print(f"  Overall: {self.working_count}/{total} working ({working_pct:.0f}%)")

        except Exception as e:
            self.log_failure(app_num, idea, str(e))

    def log_failure(self, app_num, idea, reason):
        """Log a test failure."""
        self.failing_count += 1
        print(f"  ❌ FAILED: {reason}")
        self.results.append({
            "app_num": app_num,
            "idea": idea,
            "score": 0,
            "grade": "F",
            "working": False,
            "error": reason,
            "timestamp": datetime.now().isoformat()
        })

    def run_tests(self, num_apps=100):
        """Run all tests."""
        print("=" * 80)
        print("CONTINUOUS APP GENERATION TEST")
        print("=" * 80)
        print(f"Target: 80+ working apps out of {num_apps}")
        print(f"Current OpenAI budget: $34")
        print()

        for i in range(1, num_apps + 1):
            idea = TEST_IDEAS[(i - 1) % len(TEST_IDEAS)]
            self.test_app(idea, i)

            # Check if we've hit the goal
            if self.working_count >= 80:
                print("\n" + "=" * 80)
                print(f"✅ SUCCESS! Reached {self.working_count} working apps")
                print("=" * 80)
                break

            # Save progress every 10 apps
            if i % 10 == 0:
                self.save_progress()

        self.print_summary()
        self.save_results()

    def save_progress(self):
        """Save intermediate results."""
        with open("test_progress.json", "w", encoding="utf-8") as f:
            json.dump(self.results, f, indent=2)

    def save_results(self):
        """Save final results."""
        with open("test_results.json", "w", encoding="utf-8") as f:
            json.dump(self.results, f, indent=2)

    def print_summary(self):
        """Print test summary."""
        print("\n" + "=" * 80)
        print("FINAL RESULTS")
        print("=" * 80)
        total = len(self.results)
        working_pct = (self.working_count / total * 100) if total > 0 else 0

        print(f"Total apps tested:     {total}")
        print(f"Working apps (80+):    {self.working_count}")
        print(f"Failing apps (<80):    {self.failing_count}")
        print(f"Success rate:          {working_pct:.1f}%")
        print()

        if self.working_count >= 80:
            print("🎉 TARGET ACHIEVED!")
        else:
            print(f"⚠️  Need {80 - self.working_count} more working apps")


if __name__ == "__main__":
    tester = AppTester()
    tester.run_tests(num_apps=100)
