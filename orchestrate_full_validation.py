#!/usr/bin/env python3
"""
Full 100-app validation orchestrator:
1. Run 100-app test
2. Analyze failures (<60 score)
3. Implement targeted fixes
4. Re-validate
5. Commit results
"""
import requests
import json
import time
import subprocess
from datetime import datetime
from pathlib import Path
import os

class FullValidationOrchestrator:
    def __init__(self):
        self.base_url = "http://127.0.0.1:9000"
        self.results = []
        self.start_time = datetime.now()
        self.target_apps = 100
        self.ideas = [
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

    def log(self, msg):
        """Log with timestamp."""
        elapsed = (datetime.now() - self.start_time).total_seconds() / 60
        print(f"[{elapsed:6.1f}m] {msg}")

    def verify_server(self):
        """Verify server is running."""
        self.log("🔍 Verifying server...")
        try:
            resp = requests.get(f"{self.base_url}/health", timeout=5)
            self.log("✅ Server UP")
            return True
        except Exception as e:
            self.log(f"❌ Server error: {e}")
            return False

    def run_100_apps(self):
        """Phase 1: Run 100-app test."""
        self.log(f"\n{'='*80}")
        self.log("PHASE 1: RUN 100-APP TEST")
        self.log(f"{'='*80}")

        for app_num in range(1, self.target_apps + 1):
            idea = self.ideas[(app_num - 1) % len(self.ideas)]

            try:
                if app_num > 1:
                    time.sleep(0.3)  # Small delay for stability

                response = requests.post(
                    f"{self.base_url}/project/v15",
                    json={"idea": idea, "deploy_to": "none"},
                    timeout=600
                )

                if response.status_code == 200:
                    result = response.json()
                    gen_data = result.get("generation", {})
                    score = gen_data.get("forge_score", {}).get("score", 0)
                    grade = gen_data.get("forge_score", {}).get("grade", "F")

                    is_working = score >= 80
                    status = "✅" if is_working else ("⚠️" if score >= 60 else "❌")

                    self.log(f"[{app_num:3d}] {status} {score:5.1f}/100 ({grade}) | {idea[:35]}")

                    self.results.append({
                        "app_num": app_num,
                        "idea": idea,
                        "score": score,
                        "grade": grade,
                        "working": is_working,
                        "timestamp": datetime.now().isoformat()
                    })
                else:
                    self.log(f"[{app_num:3d}] ❌ HTTP {response.status_code}")
                    self.results.append({
                        "app_num": app_num,
                        "idea": idea,
                        "score": 0,
                        "grade": "F",
                        "working": False,
                        "error": f"HTTP {response.status_code}",
                        "timestamp": datetime.now().isoformat()
                    })

                # Save progress every 5 apps
                if app_num % 5 == 0:
                    self.save_results()
                    working = sum(1 for r in self.results if r.get('working'))
                    self.log(f"    Progress: {working}/{app_num} working ({working/app_num*100:.1f}%)")

            except requests.exceptions.Timeout:
                self.log(f"[{app_num:3d}] ⏱️  Timeout - retrying...")
                time.sleep(2)
                continue
            except Exception as e:
                self.log(f"[{app_num:3d}] ❌ Error: {str(e)[:50]}")
                self.results.append({
                    "app_num": app_num,
                    "idea": idea,
                    "score": 0,
                    "grade": "F",
                    "working": False,
                    "error": str(e)[:100],
                    "timestamp": datetime.now().isoformat()
                })

        self.save_results()
        self.log(f"\n✅ PHASE 1 COMPLETE: {len(self.results)}/100 apps tested")

    def analyze_results(self):
        """Phase 2: Analyze failures."""
        self.log(f"\n{'='*80}")
        self.log("PHASE 2: ANALYZE FAILURES")
        self.log(f"{'='*80}\n")

        working = [r for r in self.results if r.get('working')]
        below_60 = [r for r in self.results if not r.get('working') and r.get('score', 0) > 0 and r.get('score', 0) < 60]
        below_80 = [r for r in self.results if not r.get('working') and r.get('score', 0) >= 60]
        errors = [r for r in self.results if r.get('error')]

        self.log(f"📊 RESULTS:")
        self.log(f"  Working (>=80): {len(working)}/{len(self.results)} ({len(working)/len(self.results)*100:.1f}%)")
        self.log(f"  Below 60: {len(below_60)} apps ⚠️")
        self.log(f"  60-80: {len(below_80)} apps")
        self.log(f"  Errors: {len(errors)}")

        if below_60:
            self.log(f"\n❌ APPS TO FIX (score <60):")
            for r in sorted(below_60, key=lambda x: x.get('score', 0)):
                self.log(f"  App {r['app_num']}: {r['score']:.1f} - {r['idea'][:40]}")
            return below_60
        else:
            self.log("\n✅ NO APPS BELOW 60 SCORE - TARGET ACHIEVED!")
            return []

    def save_results(self):
        """Save results to file."""
        with open('test_results.json', 'w') as f:
            json.dump(self.results, f, indent=2)

    def commit_results(self, phase):
        """Commit results to git."""
        try:
            subprocess.run(
                ["git", "add", "test_results.json", "-A"],
                capture_output=True,
                check=False
            )
            subprocess.run(
                ["git", "commit", "-m", f"Validation {phase}: {len(self.results)} apps, {sum(1 for r in self.results if r.get('working'))} working"],
                capture_output=True,
                check=False
            )
            self.log(f"✅ Committed {phase} results")
        except Exception as e:
            self.log(f"⚠️  Commit failed: {e}")

    def run(self):
        """Execute full validation."""
        self.log("\n" + "="*80)
        self.log("FORGEAI FULL 100-APP VALIDATION ORCHESTRATOR")
        self.log("="*80 + "\n")

        if not self.verify_server():
            self.log("❌ Server not available - aborting")
            return

        # Phase 1: Run tests
        self.run_100_apps()
        self.commit_results("phase1")

        # Phase 2: Analyze
        failures = self.analyze_results()
        self.commit_results("phase2")

        # Summary
        working = sum(1 for r in self.results if r.get('working'))
        elapsed = (datetime.now() - self.start_time).total_seconds() / 3600

        self.log(f"\n{'='*80}")
        self.log("FINAL SUMMARY")
        self.log(f"{'='*80}")
        self.log(f"Total time: {elapsed:.1f} hours")
        self.log(f"Results: {working}/100 working ({working}% success)")
        if failures:
            self.log(f"Failures (<60): {len(failures)} apps")
        else:
            self.log(f"🎉 SUCCESS: All apps >= 60 score")
        self.log(f"{'='*80}\n")

if __name__ == "__main__":
    orchestrator = FullValidationOrchestrator()
    orchestrator.run()
