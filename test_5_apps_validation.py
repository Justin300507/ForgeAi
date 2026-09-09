#!/usr/bin/env python3
"""Quick validation: 5 apps to confirm max_tokens fix is stable."""
import requests
import json
import time

BASE_URL = "http://localhost:8000/project"
APPS = [
    ("todo_app", "A simple todo application"),
    ("blog_cms", "A blog content management system"),
    ("notes_app", "A collaborative notes app"),
    ("expense_tracker", "An expense tracking application"),
    ("task_manager", "A team task management tool"),
]

print("\n" + "="*60)
print("VALIDATION TEST: 5 Apps")
print("="*60)
print(f"Endpoint: {BASE_URL}\n")

success_count = 0
for idx, (name, prompt) in enumerate(APPS, 1):
    print(f"[{idx}/5] {name:25s} ", end="", flush=True)
    start = time.time()
    
    try:
        r = requests.post(BASE_URL, json={"idea": prompt}, timeout=300)
        elapsed = time.time() - start
        
        if r.status_code == 200:
            result = r.json()
            files = len(result.get("files", []))
            print(f"OK {elapsed:6.1f}s ({files} files)")
            success_count += 1
        else:
            print(f"FAIL {elapsed:6.1f}s (HTTP {r.status_code})")
    except Exception as e:
        elapsed = time.time() - start
        print(f"ERROR {elapsed:6.1f}s ({str(e)[:30]})")
    
    time.sleep(2)

print("\n" + "="*60)
print(f"RESULT: {success_count}/5 successful ({100*success_count/5:.0f}%)")
print("="*60)

if success_count == 5:
    print("\nSUCCESS: Fix is stable and working!")
else:
    print(f"\nWARNING: Only {success_count}/5 succeeded")
