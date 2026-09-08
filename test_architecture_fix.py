#!/usr/bin/env python3
"""Quick test of the architecture repair fix."""
import requests
import json
import time
from pathlib import Path

# Generate a simple todo app
payload = {
    'idea': 'A simple todo app with create, read, update, delete todos',
    'deploy_to': 'none'
}

print("=" * 80)
print("TESTING ARCHITECTURE REPAIR FIX")
print("=" * 80)
print(f"\nGenerating: {payload['idea']}")
print("Sending request to http://localhost:8000/project/v15...")

try:
    response = requests.post('http://localhost:8000/project/v15', json=payload, timeout=300)

    if response.status_code != 200:
        print(f"\n❌ Generation failed: {response.status_code}")
        print(response.text[:500])
        exit(1)

    result = response.json()
    score = result.get('generation', {}).get('forge_score', {})

    print(f"\n✅ Generation completed!")
    print(f"   Forge Score: {score.get('score', 'N/A')}/100 ({score.get('grade', 'N/A')})")
    print(f"   Project: {result.get('generation', {}).get('project_path', 'N/A')}")

    # Check the generated route files for real implementations
    project_path = result.get('generation', {}).get('project_path')
    if project_path and Path(project_path).exists():
        routes_dir = Path(project_path) / 'app' / 'routes'

        print(f"\n📋 CHECKING ROUTE IMPLEMENTATIONS:")
        placeholder_count = 0
        real_count = 0

        for route_file in sorted(routes_dir.glob('*.py')):
            if route_file.name == '__init__.py':
                continue

            content = route_file.read_text()

            # Check for placeholder implementations
            has_return_empty_list = 'return []' in content
            has_return_empty_dict = 'return {}' in content
            has_pass_body = '\n    pass\n' in content

            is_placeholder = has_return_empty_list or has_return_empty_dict or has_pass_body

            # Check for real implementations
            has_db_query = 'db.query' in content
            has_db_add = 'db.add' in content
            has_real_logic = 'if not' in content or 'raise HTTPException' in content

            is_real = has_db_query or (has_db_add and has_real_logic)

            if is_placeholder:
                placeholder_count += 1
                status = "❌ PLACEHOLDER"
            elif is_real:
                real_count += 1
                status = "✅ REAL IMPLEMENTATION"
            else:
                status = "⚠️  UNKNOWN"

            print(f"   {route_file.name:30} {status}")

        print(f"\n📊 SUMMARY:")
        print(f"   Real implementations:  {real_count}")
        print(f"   Placeholders:          {placeholder_count}")
        print(f"   Total routes:          {real_count + placeholder_count}")

        if placeholder_count == 0 and real_count > 0:
            print(f"\n✅ SUCCESS - Architecture repair is generating real implementations!")
        elif placeholder_count > 0:
            print(f"\n⚠️  WARNING - Still found {placeholder_count} placeholder implementations")
        else:
            print(f"\n❓ Unable to determine implementation type")

except Exception as e:
    print(f"\n❌ Error: {e}")
    import traceback
    traceback.print_exc()
    exit(1)
