#!/usr/bin/env python3
import json

with open('test_results.json') as f:
    results = json.load(f)

working = [r for r in results if r.get('working')]
failed = [r for r in results if not r.get('working')]

total = len(results)
print(f"\n{'='*80}")
print(f"FORGEAI GENERATION VALIDATION TEST")
print(f"{'='*80}")
print(f"✅ Working: {len(working)}/{total} ({len(working)/total*100:.1f}% success rate)")
print(f"❌ Failed: {len(failed)}/{total}")

if working:
    scores = [r['score'] for r in working]
    print(f"\n📊 Scores (working apps only):")
    print(f"  Min: {min(scores):.1f}")
    print(f"  Max: {max(scores):.1f}")
    print(f"  Avg: {sum(scores)/len(scores):.1f}")

threshold = total * 0.80 if total > 50 else 80
if len(working) >= threshold:
    print(f"\n🎯 Target: {threshold:.0f}+ working ({threshold/total*100:.0f}%) ✅ ACHIEVED")
else:
    print(f"\n🎯 Target: {threshold:.0f}+ working ({threshold/total*100:.0f}%) | Got {len(working)} ({len(working)/total*100:.1f}%)")

# Show first 10 and last 10 for context
print(f"\n📋 First 10 apps:")
for r in results[:10]:
    status = "✅" if r.get('working') else "❌"
    print(f"  [{r['app_num']:2d}] {status} {r['score']:5.1f} | {r.get('error', r.get('grade', 'N/A'))[:50]}")

print(f"\n📋 Last 10 apps:")
for r in results[-10:]:
    status = "✅" if r.get('working') else "❌"
    print(f"  [{r['app_num']:2d}] {status} {r['score']:5.1f} | {r.get('error', r.get('grade', 'N/A'))[:50]}")

print(f"\n{'='*80}\n")
