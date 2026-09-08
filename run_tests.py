import sys
sys.path.insert(0, '.')
import requests
import json
import time
from datetime import datetime
from pathlib import Path

class DeploymentTester:
    def __init__(self):
        self.prod_url = "http://localhost:8000"
        self.results = []
        self.working_count = 0
        self.start_time = datetime.now()

    def verify_production(self):
        print("=" * 80)
        print("PRODUCTION TEST SUITE")
        print("=" * 80)
        print(f"Testing against: {self.prod_url}")
        print(f"Target: 80+ working apps (Forge Score >= 80)")
        print()

        try:
            response = requests.get(f"{self.prod_url}/health", timeout=10)
            if response.status_code == 200:
                print("? Server is LIVE")
                return True
        except Exception as e:
            print(f"? Server error: {e}")
        return False

    def test_app(self, idea, app_num):
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
            score = result.get("forge_score", 0)
            grade = result.get("grade", "F")
            is_working = score >= 80

            elapsed = (datetime.now() - self.start_time).total_seconds() / 60
            status = "?" if is_working else "?"
            
            print(f"[{app_num:3d}] {status} {score:5.1f}/100 ({grade}) | {idea[:35]:35} | {elapsed:5.1f}m")

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

            self.save_progress()

            total = len(self.results)
            if self.working_count >= 80:
                print("\n" + "=" * 80)
                print(f"?? SUCCESS! {self.working_count} working apps out of {total}!")
                print("=" * 80)
                return True

        except Exception as e:
            self.log_failure(app_num, idea, str(e)[:50])
        return False

    def log_failure(self, app_num, idea, reason):
        elapsed = (datetime.now() - self.start_time).total_seconds() / 60
        print(f"[{app_num:3d}] ? {reason:20} | {idea[:35]:35} | {elapsed:5.1f}m")
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
        with open("test_results.json", "w", encoding="utf-8") as f:
            json.dump(self.results, f, indent=2)

    def run_100_apps(self):
        test_ideas = [
            "A todo app with user login and task tracking",
            "A simple note-taking app with folders",
            "A task management system with priorities",
            "A blog platform with comments and likes",
            "A contact manager with groups",
            "A habit tracker with streaks",
            "A fitness log with workout history",
            "A recipe collection app",
            "A movie watchlist with ratings",
            "An expense tracker with categories",
        ]

        print("\n" + "=" * 80)
        print("TESTING 100 APPS")
        print("=" * 80)
        print()

        for i in range(1, 101):
            idea = test_ideas[(i - 1) % len(test_ideas)]
            if self.test_app(idea, i):
                break
            if i % 10 == 0:
                total = len(self.results)
                working_pct = (self.working_count / total * 100) if total > 0 else 0
                print(f"\n?? Checkpoint: {self.working_count}/{total} ({working_pct:.0f}%)\n")

        self.print_summary()

    def print_summary(self):
        print("\n" + "=" * 80)
        print("TEST SUMMARY")
        print("=" * 80)
        total = len(self.results)
        working_pct = (self.working_count / total * 100) if total > 0 else 0
        elapsed = (datetime.now() - self.start_time).total_seconds() / 60

        print(f"Total tested:   {total}")
        print(f"Working (80+):  {self.working_count}")
        print(f"Success rate:   {working_pct:.1f}%")
        print(f"Time elapsed:   {elapsed:.1f} minutes")
        print()

        if self.working_count >= 80:
            print("?? TARGET ACHIEVED!")
        else:
            print(f"??  Need {80 - self.working_count} more to reach 80")

if __name__ == "__main__":
    tester = DeploymentTester()
    if tester.verify_production():
        tester.run_100_apps()
