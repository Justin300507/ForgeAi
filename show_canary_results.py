#!/usr/bin/env python3
import json

with open('backend/benchmark_results/canary_history.json', encoding='utf-8') as f:
    data = json.load(f)
    latest = data['runs'][-1] if data['runs'] else None

    if latest:
        print('=' * 70)
        print('LATEST CANARY RUN RESULTS')
        print('=' * 70)
        print(f"Label:     {latest.get('label', 'N/A')}")
        print(f"Timestamp: {latest.get('timestamp', 'N/A')}")
        print()

        for result in latest.get('results', []):
            print(f"APP: {result['app']}")
            print(f"  Forge Score:   {result['forge_score']:.1f}/100 ({result['grade']})")
            print(f"  Build OK:      {result.get('build_ok', 'N/A')}")
            print(f"  Runtime OK:    {result.get('runtime_ok', 'N/A')}")
            print(f"  Fix attempts:  {result.get('fix_attempts', 'N/A')}")
            print()
