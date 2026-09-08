#!/usr/bin/env python3
"""Monitor 100-app test progress and report failures."""
import json
import time
import os

def monitor():
    """Monitor test progress."""
    last_count = 0
    while True:
        if os.path.exists('test_results.json'):
            try:
                with open('test_results.json') as f:
                    results = json.load(f)

                working = sum(1 for r in results if r.get('working'))
                low_score = [r for r in results if not r.get('working') and r.get('score', 0) > 0 and r.get('score', 0) < 60]
                errors = [r for r in results if r.get('error')]

                if len(results) > last_count:
                    print(f"\n📊 Progress: {len(results)}/100 apps | Working: {working} ({working/len(results)*100:.1f}%)")
                    if low_score:
                        print(f"⚠️  Low score (<60): {len(low_score)}")
                    if errors:
                        print(f"❌ Errors: {len(errors)}")
                    last_count = len(results)

                # Check if done
                if len(results) >= 100:
                    print(f"\n{'='*60}")
                    print(f"TEST COMPLETE: {working}/100 working ({working}%)")
                    if low_score:
                        print(f"\nAPPS TO FIX (score <60): {len(low_score)}")
                        for r in low_score:
                            print(f"  App {r['app_num']}: {r['score']:.1f} - {r['idea'][:40]}")
                    print(f"{'='*60}\n")
                    return

            except:
                pass

        time.sleep(10)

if __name__ == "__main__":
    monitor()
