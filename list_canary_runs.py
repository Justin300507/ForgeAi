#!/usr/bin/env python3
import json

with open('backend/benchmark_results/canary_history.json', encoding='utf-8') as f:
    data = json.load(f)

# Get last 3 runs
runs = data['runs'][-3:]
print('LAST 3 CANARY RUNS:')
print('=' * 80)
for i, run in enumerate(runs, 1):
    ts = run.get('timestamp', 'N/A')
    label = run.get('label', 'N/A')
    print(f'{i}. {ts} | Label: {label}')
    for result in run.get('results', []):
        print(f'   {result["app"]}: {result["forge_score"]:.1f}/100 ({result["grade"]})')
    print()
