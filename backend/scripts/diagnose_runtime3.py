"""Diagnose inventory_system runtime failure."""
import sys, subprocess, time, requests, threading
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND_ROOT))

TARGET = "inventory_system"
PROJECT = _BACKEND_ROOT.parent / "generated_projects" / TARGET

server_lines = []

process = subprocess.Popen(
    [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8003"],
    cwd=PROJECT,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
    encoding="utf-8",
    errors="replace",
)

def reader():
    for line in process.stdout:
        server_lines.append(line.rstrip())
        print(f"  SERVER: {line.rstrip()}", flush=True)

t = threading.Thread(target=reader, daemon=True)
t.start()

time.sleep(4)

try:
    r = requests.get("http://127.0.0.1:8003/docs", timeout=3)
    print(f"\nDocs: {r.status_code}")
except Exception as e:
    print(f"Docs failed: {e}")
    process.terminate()
    sys.exit(1)

# Load architecture to know which endpoints to test
import json
meta = PROJECT / "metadata.json"
arch = json.loads(meta.read_text(encoding="utf-8")).get("architecture", {})

print("\nHitting endpoints (5s timeout each)...")
import re
for ep in arch.get("api_endpoints", [])[:10]:
    method = ep.get("method", "GET").upper()
    path = re.sub(r"\{[^}]+\}", "1", ep.get("path", ""))
    url = f"http://127.0.0.1:8003{path}"
    try:
        if method == "GET":
            r = requests.get(url, timeout=5)
        elif method == "POST":
            r = requests.post(url, json={"name": "test", "quantity": 10, "price": 9.99, "sku": "TEST-001"}, timeout=5)
        elif method == "PUT":
            r = requests.put(url, json={"name": "test", "quantity": 10, "price": 9.99}, timeout=5)
        elif method == "DELETE":
            r = requests.delete(url, timeout=5)
        else:
            continue
        status = "OK" if r.status_code < 500 else "FAIL"
        print(f"  [{status}] {method} {path} -> {r.status_code}: {r.text[:100]}")
    except requests.exceptions.Timeout:
        print(f"  [TIMEOUT] {method} {path}")
    except Exception as e:
        print(f"  [ERR] {method} {path}: {e}")

time.sleep(1)
process.terminate()
try: process.wait(timeout=5)
except: process.kill()

print("\n\n=== SERVER LOG (errors only) ===")
in_tb = False
for line in server_lines:
    if "ERROR" in line or "Traceback" in line or "Error" in line or "Exception" in line:
        in_tb = True
    if in_tb:
        print(f"  {line}")
    if in_tb and line.strip() == "":
        in_tb = False
