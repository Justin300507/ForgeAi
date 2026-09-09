#!/usr/bin/env python3
"""
Generate and test 30 diverse apps to find failure patterns.
Collects results, identifies failures, and prepares for fixing.
"""

import json
import requests
import time
from pathlib import Path
from datetime import datetime

BASE_URL = "http://localhost:8000"
ENDPOINT = "/project"  # Use /project (proven stable) not /project/v15 (encoding issue in browser session)
TIMEOUT = 600  # 10 minutes per app

def load_prompts():
    """Load test app prompts."""
    with open("test_30_apps_prompts.json") as f:
        return json.load(f)

def generate_app(name: str, prompt: str, index: int, total: int) -> dict:
    """Generate a single app and return result."""
    print(f"\n[{index:2d}/{total}] {name:25s} ", end="", flush=True)
    start = time.time()

    try:
        response = requests.post(
            f"{BASE_URL}{ENDPOINT}",
            json={"idea": prompt},
            timeout=TIMEOUT
        )
        elapsed = time.time() - start

        if response.status_code == 200:
            try:
                result = response.json()
                status = "SUCCESS"
                print(f"OK {elapsed:6.1f}s")
                return {
                    "name": name,
                    "prompt": prompt,
                    "status": "success",
                    "time": elapsed,
                    "forge_score": result.get("forge_score", 0),
                    "compile": result.get("validation", {}).get("compile_success", False),
                    "runtime": result.get("validation", {}).get("runtime_success", False),
                    "crud": result.get("validation", {}).get("crud_success", False),
                }
            except json.JSONDecodeError as e:
                print(f"FAIL {elapsed:6.1f}s (JSON parse error)")
                return {
                    "name": name,
                    "prompt": prompt,
                    "status": "error",
                    "error": "JSON parse error",
                    "time": elapsed,
                    "response_text": response.text[:200]
                }
        else:
            print(f"FAIL {elapsed:6.1f}s (HTTP {response.status_code})")
            return {
                "name": name,
                "prompt": prompt,
                "status": "error",
                "error": f"HTTP {response.status_code}",
                "time": elapsed,
                "response_text": response.text[:200]
            }
    except requests.Timeout:
        print(f"FAIL TIMEOUT (>{TIMEOUT}s)")
        return {
            "name": name,
            "prompt": prompt,
            "status": "error",
            "error": "Timeout",
            "time": TIMEOUT
        }
    except Exception as e:
        elapsed = time.time() - start
        print(f"FAIL {elapsed:6.1f}s (Exception: {str(e)[:50]})")
        return {
            "name": name,
            "prompt": prompt,
            "status": "error",
            "error": str(e)[:100],
            "time": elapsed
        }

def main():
    print("=" * 80)
    print("FORGEAI 30-APP GENERATION TEST")
    print("=" * 80)
    print(f"Timestamp: {datetime.now().isoformat()}")
    print(f"Endpoint:  {BASE_URL}{ENDPOINT}")
    print(f"Note:      Using /project (stable) not /project/v15 (encoding issue in browser)")
    print(f"Timeout:   {TIMEOUT}s per app")

    # Test server connectivity
    print("\nChecking server health...", end="", flush=True)
    try:
        response = requests.get(f"{BASE_URL}/health", timeout=5)
        if response.status_code == 200:
            print(" OK - Server ready")
        else:
            print(f" FAIL - Server returned {response.status_code}")
            return
    except Exception as e:
        print(f" FAIL - Cannot connect: {e}")
        print("\nMake sure server is running:")
        print("  $env:PYTHONIOENCODING = 'utf-8'")
        print("  cd backend")
        print("  python -m uvicorn main:app --port 8000")
        return

    prompts = load_prompts()
    results = []

    print(f"\nGenerating {len(prompts)} apps...\n")
    print("Index  Name                      Status     Time")
    print("-" * 60)

    for idx, app in enumerate(prompts, 1):
        result = generate_app(app["name"], app["prompt"], idx, len(prompts))
        results.append(result)
        time.sleep(5)  # Rate limiting (server: 10 req/60s = min 6s delay)

    # Analysis
    print("\n" + "=" * 80)
    print("RESULTS ANALYSIS")
    print("=" * 80)

    successful = [r for r in results if r["status"] == "success"]
    failed = [r for r in results if r["status"] == "error"]

    print(f"\nTotal:       {len(results)}")
    print(f"Successful:  {len(successful)} ({100*len(successful)/len(results):.1f}%)")
    print(f"Failed:      {len(failed)} ({100*len(failed)/len(results):.1f}%)")

    if successful:
        avg_time = sum(r["time"] for r in successful) / len(successful)
        scores = [r.get("forge_score", 0) for r in successful]
        scores = [s for s in scores if isinstance(s, (int, float))]
        avg_score = sum(scores) / len(scores) if scores else 0
        print(f"\nSuccessful Apps:")
        print(f"  Avg Time:    {avg_time:.1f}s")
        print(f"  Avg Score:   {avg_score:.1f}")
        print(f"  Min Time:    {min(r['time'] for r in successful):.1f}s")
        print(f"  Max Time:    {max(r['time'] for r in successful):.1f}s")

        compile_count = sum(1 for r in successful if r.get("compile"))
        runtime_count = sum(1 for r in successful if r.get("runtime"))
        crud_count = sum(1 for r in successful if r.get("crud"))

        print(f"  Compile:     {compile_count}/{len(successful)}")
        print(f"  Runtime:     {runtime_count}/{len(successful)}")
        print(f"  CRUD:        {crud_count}/{len(successful)}")

    if failed:
        print(f"\nFailed Apps ({len(failed)}):")
        error_types = {}
        for r in failed:
            error = r.get("error", "Unknown")
            error_types[error] = error_types.get(error, 0) + 1

        for error, count in sorted(error_types.items(), key=lambda x: -x[1]):
            print(f"  {error}: {count}")
            # Show examples
            examples = [r for r in failed if r.get("error") == error][:2]
            for ex in examples:
                print(f"    • {ex['name']}")

    # Save results
    output_file = f"generation_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(output_file, "w") as f:
        json.dump({
            "timestamp": datetime.now().isoformat(),
            "endpoint": f"{BASE_URL}{ENDPOINT}",
            "total": len(results),
            "successful": len(successful),
            "failed": len(failed),
            "results": results
        }, f, indent=2)

    print(f"\nResults saved to: {output_file}")

    # Save failure summary for fixing
    if failed:
        failures_file = "generation_failures.json"
        with open(failures_file, "w") as f:
            json.dump(failed, f, indent=2)
        print(f"Failures saved to: {failures_file}")

        print("\n" + "=" * 80)
        print("NEXT STEPS")
        print("=" * 80)
        print(f"Review failures in {failures_file}")
        print("Identify common patterns")
        print("Fix issues in the pipeline")
        print("Re-run test")

if __name__ == "__main__":
    main()
