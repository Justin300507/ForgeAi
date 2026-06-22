"""
Deploy the generated job_board_platform to the existing Railway service.
Overwrites the current deployment temporarily to prove V11 works.
After verification, re-deploys ForgeAI.
"""
import sys, subprocess, time, shutil
sys.path.insert(0, '.')
from app.services.deployment_service import prepare_deployment_files
from app.runtime.deployment_validator import validate_deployment_with_retry

PROJECT_PATH = r'C:\Users\jerry\onedrive\Desktop\forgeai\generated_projects\job_board_platform'
BACKEND_PATH = r'C:\Users\jerry\onedrive\Desktop\forgeai\backend'
RAILWAY_EXE  = shutil.which("railway") or "railway"

# Known working IDs from `railway service list`
PROJECT_ID = "cb818094-b94f-4693-805e-cb2aec4a82bc"
SERVICE_ID  = "37a3a02a-304f-4e9f-985b-eaba95fde45c"
LIVE_URL    = "https://laudable-encouragement-production-2777.up.railway.app"

import os
env = {**os.environ, "RAILWAY_PROJECT_ID": PROJECT_ID, "RAILWAY_SERVICE_ID": SERVICE_ID}


def run(cmd, cwd, timeout=300):
    r = subprocess.run([RAILWAY_EXE] + cmd, cwd=cwd, env=env,
                       capture_output=True, text=True, timeout=timeout)
    return r


print("=" * 60)
print("  STEP 1 — Write Dockerfile to generated project")
print("=" * 60)
prepare_deployment_files(PROJECT_PATH)
print("  Done.")

print()
print("=" * 60)
print("  STEP 2 — Deploy job_board_platform to Railway")
print("=" * 60)
up = run(["up", "--detach"], cwd=PROJECT_PATH)
print("stdout:", up.stdout)
print("stderr:", up.stderr)
print("exit  :", up.returncode)

if up.returncode != 0:
    print("\n  Deploy FAILED — restoring ForgeAI")
    run(["up", "--detach"], cwd=BACKEND_PATH)
    sys.exit(1)

print()
print("=" * 60)
print("  STEP 3 — Health check (job board at", LIVE_URL, ")")
print("=" * 60)
health = validate_deployment_with_retry(LIVE_URL, retries=8, wait=20)
print("Health OK :", health['success'])
print("Score     :", health['score'])
for path, check in health['checks'].items():
    print(" ", path, "->", check.get('status_code', check.get('error', '?')), " ok =", check.get('ok'))

print()
print("=" * 60)
print("  STEP 4 — Restore ForgeAI backend")
print("=" * 60)
restore = run(["up", "--detach"], cwd=BACKEND_PATH)
print("Restore exit:", restore.returncode)
print("Restore:", restore.stdout or restore.stderr)

print()
print("=" * 60)
if health['success']:
    print("  PROOF: Job board deployed and health checks PASSED")
    print("  URL:", LIVE_URL)
else:
    print("  Deploy succeeded but health check failed:", health.get('error'))
print("=" * 60)
