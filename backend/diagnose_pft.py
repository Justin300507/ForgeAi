"""Diagnose personal_finance_tracker runtime failure."""
import sys, subprocess, time, threading
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent))

TARGET = "personal_finance_tracker"
PROJECT = Path(__file__).parent.parent / "generated_projects" / TARGET

server_lines = []
process = subprocess.Popen(
    [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8004"],
    cwd=PROJECT,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True, encoding="utf-8", errors="replace",
)

def reader():
    for line in process.stdout:
        server_lines.append(line.rstrip())

t = threading.Thread(target=reader, daemon=True)
t.start()
time.sleep(5)

process.terminate()
try: process.wait(timeout=5)
except: process.kill()

print("=== SERVER OUTPUT ===")
for line in server_lines:
    print(f"  {line}")
