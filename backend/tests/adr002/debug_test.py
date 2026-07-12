import os, sys
import sys
import io

# Force UTF-8 output
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

src_path = os.path.join(
    os.path.dirname(__file__), "..", "..", "app", "runtime", "user_journey_runner.py"
)
with open(src_path, "r", encoding="utf-8") as f:
    src = f.read()

seed_block_start = src.index("Reference-data seed")
seed_block = src[seed_block_start:seed_block_start + 1200]

print("Checks:")
print(f"  'seed_summary' in block: {'seed_summary' in seed_block}")
print(f"  '.json()' in block: {'.json()' in seed_block}")
print(f"  Total seed block length: {len(seed_block)}")

# Find where they appear
seed_summary_idx = seed_block.find('seed_summary')
json_idx = seed_block.find('.json()')
print(f"  'seed_summary' at offset: {seed_summary_idx}")
print(f"  '.json()' at offset: {json_idx}")
