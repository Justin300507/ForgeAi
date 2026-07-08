"""
Shared source of truth for Render backend build/start commands.

Both render_provider.py (live REST-API-driven deploy, used by the
automated V15Pipeline flow) and deployment_config_service.py (generates
render.yaml for the manual Render Blueprint flow) previously hardcoded
these two strings independently -- currently identical by coincidence,
not by guarantee. Extracted here so they can never silently drift.

See docs/V16_DEPLOYMENT_RELIABILITY_AUDIT.md, Finding #1.
"""

RENDER_BACKEND_BUILD_COMMAND = "pip install -r app/requirements.txt"
RENDER_BACKEND_START_COMMAND = "uvicorn app.main:app --host 0.0.0.0 --port $PORT"
