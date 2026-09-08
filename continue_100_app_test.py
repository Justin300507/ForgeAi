#!/usr/bin/env python3
"""
Continue 100-app test from app 47.
Loads existing results and continues testing.
"""
import requests
import json
import time
from datetime import datetime
import sys

# Load existing results
try:
    with open('test_results.json', 'r') as f:
        results = json.load(f)
    print(f"✅ Loaded {len(results)} existing results")
except:
    results = []

# Configuration
BASE_URL = "http://127.0.0.1:9000"
TARGET_APPS = 100
TARGET_WORKING = 80

# App ideas to cycle through
ideas = [
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

# Verify server is up
print(f"\n📡 Checking server at {BASE_URL}...")
try:
    resp = requests.get(f"{BASE_URL}/health", timeout=5)
    print(f"✅ Server is UP (HTTP {resp.status_code})")
except Exception as e:
    print(f"❌ Server not responding: {e}")
    sys.exit(1)

start_time = datetime.now()
working_count = sum(1 for r in results if r.get('working'))

# Continue from where we left off
next_app = len(results) + 1 if results else 1
print(f"\n🚀 Starting from app {next_app}")
print(f"📊 Current: {working_count}/{len(results)} working\n")

for app_num in range(next_app, TARGET_APPS + 1):
    idea = ideas[(app_num - 1) % len(ideas)]

    try:
        payload = {"idea": idea, "deploy_to": "none"}
        response = requests.post(
            f"{BASE_URL}/project/v15",
            json=payload,
            timeout=300
        )

        if response.status_code == 200:
            result = response.json()
            gen_data = result.get("generation", {})
            score = gen_data.get("forge_score", {}).get("score", 0)
            grade = gen_data.get("forge_score", {}).get("grade", "F")

            is_working = score >= 80
            if is_working:
                working_count += 1

            elapsed = (datetime.now() - start_time).total_seconds() / 60
            status = "✅" if is_working else "❌"

            print(f"[{app_num:3d}] {status} {score:5.1f}/100 ({grade}) | {idea[:40]:40} | {elapsed:5.1f}m | {working_count}/{app_num}")

            results.append({
                "app_num": app_num,
                "idea": idea,
                "score": score,
                "grade": grade,
                "working": is_working,
                "timestamp": datetime.now().isoformat()
            })
        else:
            print(f"[{app_num:3d}] ❌ HTTP {response.status_code} | {idea[:40]:40}")
            results.append({
                "app_num": app_num,
                "idea": idea,
                "score": 0,
                "grade": "F",
                "working": False,
                "error": f"HTTP {response.status_code}",
                "timestamp": datetime.now().isoformat()
            })

        # Save progress
        with open('test_results.json', 'w') as f:
            json.dump(results, f, indent=2)

        # Check if goal reached
        if working_count >= TARGET_WORKING:
            print(f"\n🎉 SUCCESS! Reached {working_count} working apps!")
            break

    except Exception as e:
        print(f"[{app_num:3d}] ❌ Error: {str(e)[:60]}")
        results.append({
            "app_num": app_num,
            "idea": idea,
            "score": 0,
            "grade": "F",
            "working": False,
            "error": str(e)[:100],
            "timestamp": datetime.now().isoformat()
        })
        with open('test_results.json', 'w') as f:
            json.dump(results, f, indent=2)

# Final summary
print(f"\n{'='*80}")
total_time = (datetime.now() - start_time).total_seconds() / 60
final_count = sum(1 for r in results if r.get('working'))
success_rate = (final_count / len(results) * 100) if results else 0
print(f"FINAL RESULTS: {final_count}/{len(results)} working ({success_rate:.1f}%)")
print(f"Total time: {total_time:.1f} minutes")
print(f"Target: {TARGET_WORKING}+ working ✅" if final_count >= TARGET_WORKING else f"Target: {TARGET_WORKING}+ working ⚠️")
print(f"{'='*80}")
