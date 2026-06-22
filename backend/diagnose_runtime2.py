"""Start uvicorn manually and hit the hanging endpoint to get the real error."""
import sys, subprocess, time, requests, threading
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent))

TARGET = "expense_tracker_app"
PROJECT = Path(__file__).parent.parent / "generated_projects" / TARGET

# Collect server output in background
server_lines = []

process = subprocess.Popen(
    [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8002"],
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

# Wait for startup
time.sleep(4)

# Hit /docs to confirm alive
try:
    r = requests.get("http://127.0.0.1:8002/docs", timeout=3)
    print(f"\nDocs: {r.status_code}")
except Exception as e:
    print(f"Docs failed: {e}")

# Hit first endpoint that hangs
print("\nHitting POST /categories (5s timeout)...")
try:
    r = requests.post("http://127.0.0.1:8002/categories", json={"name": "test", "description": "test"}, timeout=5)
    print(f"  Response: {r.status_code} — {r.text[:200]}")
except requests.exceptions.Timeout:
    print("  TIMED OUT after 5s")
except Exception as e:
    print(f"  Error: {e}")

time.sleep(1)

# Kill server
process.terminate()
try: process.wait(timeout=5)
except: process.kill()

print("\n\n=== FULL SERVER LOG ===")
for line in server_lines:
    print(f"  {line}")
