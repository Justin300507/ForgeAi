"""
ForgeBench v1.0 subprocess worker -- runs exactly one app's
generate_project_v15() call and writes the result to a JSON file.

Spawned by forgebench_v1.py's main process with a hard timeout, so a
single hung generation (an intermittent, no-timeout blocking call
observed live during this run -- see docs/FORGEBENCH_V1_REPORT.md)
can be killed and the benchmark can continue automatically, instead of
requiring manual tasklist/taskkill intervention for every hang.

Usage: python _forgebench_worker.py <input_json> <output_json>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND_ROOT))
sys.path.insert(0, str(_BACKEND_ROOT / "scripts"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from forgebench_v1 import _run_one


def main():
    input_path, output_path = sys.argv[1], sys.argv[2]
    spec = json.loads(Path(input_path).read_text(encoding="utf-8"))
    result = _run_one(spec["app"], spec["idea"], spec["provider"])
    Path(output_path).write_text(
        json.dumps(result, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
