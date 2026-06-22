"""
V11 Deployment Service

Responsibilities:
  - Generate Dockerfile + .dockerignore + railway.json for a generated project
  - Dispatch to the right deployment provider (Railway → Render → Fly.io)
  - Save deployment metadata to deployment_metadata.json
"""
import json
import time
from pathlib import Path
from typing import Literal

from app.deployments.base_provider import DeploymentResult

METADATA_FILE = Path(__file__).parent.parent.parent / "deployment_metadata.json"

DeploymentProvider = Literal["railway", "render", "flyio"]

DOCKERFILE = """\
FROM python:3.11-slim
WORKDIR /code
COPY app/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
"""

DOCKERIGNORE = """\
__pycache__
*.pyc
*.pyo
*.pyd
.env
*.db
*.sqlite3
venv/
.venv/
.git/
node_modules/
frontend/
.railway/
"""

RAILWAY_JSON = json.dumps({
    "$schema": "https://railway.app/railway.schema.json",
    "build": {"builder": "DOCKERFILE"},
    "deploy": {
        "healthcheckPath": "/health",
        "healthcheckTimeout": 100,
        "restartPolicyType": "ON_FAILURE",
        "restartPolicyMaxRetries": 3,
    },
}, indent=2)


def prepare_deployment_files(project_path: str) -> None:
    """Write Dockerfile, .dockerignore, and railway.json into the project directory."""
    root = Path(project_path)
    (root / "Dockerfile").write_text(DOCKERFILE)
    (root / ".dockerignore").write_text(DOCKERIGNORE)
    (root / "railway.json").write_text(RAILWAY_JSON)
    print(f"  [Deploy] Deployment files written to {root}")


def get_provider(name: DeploymentProvider):
    """Return a provider instance by name."""
    if name == "railway":
        from app.deployments.railway_provider import RailwayProvider
        return RailwayProvider()
    if name == "render":
        from app.deployments.render_provider import RenderProvider
        return RenderProvider()
    if name == "flyio":
        from app.deployments.flyio_provider import FlyioProvider
        return FlyioProvider()
    raise ValueError(f"Unknown deployment provider: {name!r}")


def deploy_project(
    project_path: str,
    project_name: str,
    provider_name: DeploymentProvider = "railway",
    env_vars: dict | None = None,
) -> DeploymentResult:
    """
    Full deployment flow:
      1. Write Dockerfile + railway.json
      2. Call provider.deploy()
      3. Save metadata
    """
    print(f"\n{'='*60}")
    print(f"  V11 DEPLOYMENT — {provider_name.upper()}")
    print(f"  Project: {project_name}")
    print(f"{'='*60}")

    prepare_deployment_files(project_path)

    provider = get_provider(provider_name)
    result = provider.deploy(project_path, project_name, env_vars)

    _save_metadata(project_name, provider_name, result)

    if result.success:
        print(f"  [Deploy] Live at: {result.url}")
    else:
        print(f"  [Deploy] Failed: {result.error}")

    return result


def _save_metadata(
    project_name: str,
    provider_name: str,
    result: DeploymentResult,
) -> None:
    record = {
        "project_name": project_name,
        "provider": provider_name,
        "deployed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "success": result.success,
        "url": result.url,
        "deploy_id": result.deploy_id,
        "error": result.error,
        "metadata": result.metadata,
    }

    data: dict = {"deployments": []}
    if METADATA_FILE.exists():
        try:
            data = json.loads(METADATA_FILE.read_text())
        except Exception:
            pass

    data.setdefault("deployments", []).append(record)
    METADATA_FILE.write_text(json.dumps(data, indent=2))


def get_deployment_history(limit: int = 20) -> list[dict]:
    if not METADATA_FILE.exists():
        return []
    try:
        data = json.loads(METADATA_FILE.read_text())
        return data.get("deployments", [])[-limit:]
    except Exception:
        return []
