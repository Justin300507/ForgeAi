#!/usr/bin/env python3
"""
Run fresh 100-app test with better server stability and error tracking.
Track failures (<60 score) for targeted fixes.
"""
import requests
import json
import time
from datetime import datetime
import sys

BASE_URL = "http://127.0.0.1:9000"
TARGET_APPS = 100

# App ideas for variety
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

results = []
working = 0
failures = []  # Track failures < 60
start_time = datetime.now()

print(f"\n{'='*80}")
print("FORGEAI 100-APP VALIDATION TEST - FRESH RUN")
print(f"{'='*80}\n")

# Verify server
print(f"🔍 Checking server at {BASE_URL}...")
try:
    resp = requests.get(f"{BASE_URL}/health", timeout=5)
    print(f"✅ Server UP\n")
except Exception as e:
    print(f"❌ Server not responding: {e}\n")
    sys.exit(1)

for app_num in range(1, TARGET_APPS + 1):
    idea = ideas[(app_num - 1) % len(ideas)]

    try:
        # Add small delay between requests for stability
        if app_num > 1:
            time.sleep(0.5)

        response = requests.post(
            f"{BASE_URL}/project/v15",
            json={"idea": idea, "deploy_to": "none"},
            timeout=300
        )

        if response.status_code == 200:
            result = response.json()
            gen_data = result.get("generation", {})
            score = gen_data.get("forge_score", {}).get("score", 0)
            grade = gen_data.get("forge_score", {}).get("grade", "F")

            is_working = score >= 80
            if is_working:
                working += 1

            # Track failures
            if score < 60:
                failures.append({"app": app_num, "idea": idea, "score": score, "grade": grade})

            elapsed = (datetime.now() - start_time).total_seconds() / 60
            status = "✅" if is_working else "⚠️" if score >= 60 else "❌"

            print(f"[{app_num:3d}] {status} {score:5.1f}/100 ({grade}) | {idea[:35]:35} | {elapsed:5.1f}m")

            results.append({
                "app_num": app_num,
                "idea": idea,
                "score": score,
                "grade": grade,
                "working": is_working,
                "timestamp": datetime.now().isoformat()
            })
        else:
            print(f"[{app_num:3d}] ❌ HTTP {response.status_code}")
            results.append({
                "app_num": app_num,
                "idea": idea,
                "score": 0,
                "grade": "F",
                "working": False,
                "error": f"HTTP {response.status_code}",
                "timestamp": datetime.now().isoformat()
            })
            failures.append({"app": app_num, "idea": idea, "error": f"HTTP {response.status_code}"})

        # Save progress every 5 apps
        if app_num % 5 == 0:
            with open('test_results.json', 'w') as f:
                json.dump(results, f, indent=2)

    except requests.exceptions.ConnectionError:
        print(f"[{app_num:3d}] ❌ Connection error - server may have crashed")
        results.append({
            "app_num": app_num,
            "idea": idea,
            "score": 0,
            "grade": "F",
            "working": False,
            "error": "Connection error",
            "timestamp": datetime.now().isoformat()
        })
        failures.append({"app": app_num, "idea": idea, "error": "Connection error"})
        # Try to wait for server recovery
        time.sleep(2)

    except Exception as e:
        print(f"[{app_num:3d}] ❌ Error: {str(e)[:50]}")
        results.append({
            "app_num": app_num,
            "idea": idea,
            "score": 0,
            "grade": "F",
            "working": False,
            "error": str(e)[:100],
            "timestamp": datetime.now().isoformat()
        })
        failures.append({"app": app_num, "idea": idea, "error": str(e)[:50]})

# Final save
with open('test_results.json', 'w') as f:
    json.dump(results, f, indent=2)

# Summary
print(f"\n{'='*80}")
total_time = (datetime.now() - start_time).total_seconds() / 60
success_rate = (working / len(results) * 100) if results else 0

print(f"RESULTS: {working}/{len(results)} working ({success_rate:.1f}%)")
print(f"Time: {total_time:.1f} minutes")

if failures:
    print(f"\n⚠️  FAILURES TO FIX ({len(failures)} apps):")
    below_60 = [f for f in failures if 'score' in f and f.get('score', 0) < 60]
    errors = [f for f in failures if 'error' in f]

    if below_60:
        print(f"\n  Score < 60:")
        for f in below_60[:10]:
            print(f"    App {f['app']}: {f['score']:.1f}/100 - {f['idea'][:40]}")

    if errors:
        print(f"\n  Errors ({len(errors)} apps):")
        error_types = {}
        for f in errors:
            err = f.get('error', 'Unknown')[:40]
            error_types[err] = error_types.get(err, 0) + 1
        for err_type, count in sorted(error_types.items(), key=lambda x: -x[1])[:5]:
            print(f"    {count}x {err_type}")

print(f"\n{'='*80}\n")
