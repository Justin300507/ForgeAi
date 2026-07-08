"""
Render deployment provider — uses Render REST API.

Requires:
  - RENDER_API_KEY in backend/.env (from dashboard.render.com → Account Settings → API Keys)
  - RENDER_OWNER_ID in backend/.env (your Render user/team ID)

Flow:
  1. Create a PostgreSQL database on Render (free tier)
  2. Create a Python web service linked to the GitHub repo
  3. Create a Static Site for the frontend
  4. Trigger deploys
  5. Poll for live URL and health

If GITHUB_REPO_URL is not provided (app hasn't been pushed yet), falls back to
render.yaml instructions only and returns the dashboard URL.
"""
import os
import time
from typing import Optional

import requests

from app.deployments.base_provider import BaseDeploymentProvider, DeploymentResult
from app.deployments.render_config import RENDER_BACKEND_BUILD_COMMAND, RENDER_BACKEND_START_COMMAND

RENDER_API = "https://api.render.com/v1"


def _headers(api_key: str) -> dict:
    return {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}


def _slug(name: str) -> str:
    return name.lower().replace("_", "-").replace(" ", "-")[:60]


class RenderProvider(BaseDeploymentProvider):
    """Deploy a generated project to Render using their REST API."""

    def __init__(self) -> None:
        self.api_key = os.getenv("RENDER_API_KEY")
        self._owner_id: str | None = os.getenv("RENDER_OWNER_ID")  # optional, auto-fetched if missing

    def _check(self) -> str | None:
        if not self.api_key:
            return "RENDER_API_KEY not set in .env — get it from dashboard.render.com → Account → API Keys"
        return None

    @property
    def owner_id(self) -> str | None:
        if self._owner_id:
            return self._owner_id
        # Auto-discover from API key — GET /owners returns the account linked to this key
        try:
            resp = requests.get(
                f"{RENDER_API}/owners?limit=1",
                headers=_headers(self.api_key),
                timeout=15,
            )
            if resp.ok:
                items = resp.json()
                if items and isinstance(items, list):
                    self._owner_id = items[0].get("owner", {}).get("id")
                    print(f"  [Render] Auto-discovered owner_id: {self._owner_id}")
        except Exception as e:
            print(f"  [Render] Could not fetch owner_id: {e}")
        return self._owner_id

    def _get(self, path: str) -> requests.Response:
        return requests.get(f"{RENDER_API}{path}", headers=_headers(self.api_key), timeout=30)

    def _post(self, path: str, body: dict) -> requests.Response:
        return requests.post(f"{RENDER_API}{path}", headers=_headers(self.api_key), json=body, timeout=30)

    def _find_service(self, name: str) -> Optional[dict]:
        resp = self._get(f"/services?name={name}&limit=5")
        if resp.ok:
            services = resp.json()
            for item in services:
                svc = item.get("service", item)
                if svc.get("name") == name:
                    return svc
        return None

    def _create_web_service(
        self,
        name: str,
        repo_url: str,
        branch: str = "main",
        env_vars: dict | None = None,
    ) -> Optional[dict]:
        extra_env = [{"key": k, "value": v} for k, v in (env_vars or {}).items()]
        extra_env.append({"key": "SECRET_KEY", "generateValue": True})
        payload = {
            "type": "web_service",
            "name": name,
            "ownerId": self.owner_id,
            "repo": repo_url,
            "branch": branch,
            "autoDeploy": "yes",
            "serviceDetails": {
                "env": "python",
                "envSpecificDetails": {
                    "buildCommand": RENDER_BACKEND_BUILD_COMMAND,
                    "startCommand": RENDER_BACKEND_START_COMMAND,
                },
                "plan": "free",
                "region": "oregon",
                "healthCheckPath": "/health",
                "envVars": extra_env,
            },
        }
        resp = self._post("/services", payload)
        if resp.ok:
            data = resp.json()
            return data.get("service", data)
        print(f"  [Render] Create web service failed: {resp.status_code} {resp.text[:300]}")
        return None

    def _create_static_site(
        self,
        name: str,
        repo_url: str,
        branch: str = "main",
        api_url: str = "",
    ) -> Optional[dict]:
        payload = {
            "type": "static_site",
            "name": name,
            "ownerId": self.owner_id,
            "repo": repo_url,
            "branch": branch,
            "autoDeploy": "yes",
            "serviceDetails": {
                "buildCommand": "npm ci && npm run build",
                "publishPath": "./dist",
                "envVars": [
                    {"key": "VITE_API_URL", "value": api_url},
                ] if api_url else [],
                "routes": [{"type": "rewrite", "source": "/*", "destination": "/index.html"}],
            },
        }
        resp = self._post("/services", payload)
        if resp.ok:
            data = resp.json()
            return data.get("service", data)
        print(f"  [Render] Create static site failed: {resp.status_code} {resp.text[:300]}")
        return None

    def _trigger_deploy(self, service_id: str) -> Optional[str]:
        # Render's POST /deploys returns 202 Accepted with an EMPTY body (not
        # JSON) -- resp.json() on that raises JSONDecodeError even though the
        # deploy was accepted. Only parse JSON when there's actually content.
        resp = self._post(f"/services/{service_id}/deploys", {"clearCache": "do_not_clear"})
        if resp.ok:
            if not resp.content:
                return None
            try:
                deploy = resp.json()
                return deploy.get("id")
            except ValueError:
                return None
        return None

    def _poll_service_url(self, service_id: str, attempts: int = 4, wait: int = 2) -> Optional[str]:
        for i in range(attempts):
            resp = self._get(f"/services/{service_id}")
            if resp.ok:
                svc = resp.json().get("service", resp.json())
                url = svc.get("serviceDetails", {}).get("url") or svc.get("defaultURL") or svc.get("url")
                if url:
                    return url if url.startswith("http") else f"https://{url}"
            if i < attempts - 1:
                print(f"  [Render] Waiting for service URL (attempt {i+1}/{attempts})...")
                time.sleep(wait)
        return None

    def _extract_url_from_service(self, svc: dict) -> Optional[str]:
        """Extract URL from a service dict (creation or poll response)."""
        url = svc.get("serviceDetails", {}).get("url") or svc.get("defaultURL") or svc.get("url")
        if url:
            return url if url.startswith("http") else f"https://{url}"
        return None

    # ── public interface ──────────────────────────────────────────────────────

    def deploy(
        self,
        project_path: str,
        project_name: str,
        env_vars: dict | None = None,
    ) -> DeploymentResult:
        err = self._check()
        if err:
            return DeploymentResult(success=False, url=None, logs="", error=err,
                                    deploy_id=None, provider="render")

        # Expect github_repo_url passed via env_vars dict
        repo_url = (env_vars or {}).pop("GITHUB_REPO_URL", None)
        if not repo_url:
            return DeploymentResult(
                success=False, url=None, logs="",
                error="GITHUB_REPO_URL required — push to GitHub first, then deploy to Render",
                deploy_id=None, provider="render",
            )

        slug = _slug(project_name)
        logs: list[str] = []

        # Create or find backend web service
        print(f"  [Render] Creating backend service '{slug}-backend'...")
        backend_name = f"{slug}-backend"
        service_id = None
        backend_url = None
        existing = self._find_service(backend_name)
        if existing:
            service_id = existing.get("id")
            backend_url = self._extract_url_from_service(existing)
            logs.append(f"Reusing existing Render service: {backend_name}")
            self._trigger_deploy(service_id)
        else:
            svc = self._create_web_service(backend_name, repo_url, env_vars=env_vars)
            if not svc:
                return DeploymentResult(
                    success=False, url=None, logs="\n".join(logs),
                    error="Failed to create Render web service — check RENDER_API_KEY and RENDER_OWNER_ID",
                    deploy_id=None, provider="render",
                )
            service_id = svc.get("id")
            # URL may be in creation response — try that first, then quick poll
            backend_url = self._extract_url_from_service(svc)
            if not backend_url and service_id:
                backend_url = self._poll_service_url(service_id)
            logs.append(f"Created Render service: {backend_name} → {backend_url or 'URL pending'}")

        print(f"  [Render] Backend queued: {backend_url or 'URL pending'} (building in background ~5 min)")
        logs.append(f"Backend URL: {backend_url or 'not ready yet'} (Render free tier builds in ~5 min)")

        return DeploymentResult(
            success=bool(backend_url or service_id),
            url=backend_url,
            logs="\n".join(logs),
            error=None,
            deploy_id=service_id,
            provider="render",
            metadata={
                "backend_url": backend_url,
                "frontend_url": None,
                "backend_service_id": service_id,
                "backend_status": "building",
            },
        )

    def get_logs(self, deploy_id: str) -> str:
        if not self.api_key or not deploy_id:
            return "No API key or deploy ID"
        resp = self._get(f"/services/{deploy_id}/log-stream")
        return resp.text[:2000] if resp.ok else f"Could not fetch logs: {resp.status_code}"

    def get_status(self, deploy_id: str) -> str:
        if not self.api_key or not deploy_id:
            return "unknown"
        resp = self._get(f"/services/{deploy_id}")
        if resp.ok:
            svc = resp.json().get("service", resp.json())
            return svc.get("serviceDetails", {}).get("pullRequestPreviewsEnabled") and "live" or "deploying"
        return "unknown"

    def delete(self, deploy_id: str) -> bool:
        if not self.api_key or not deploy_id:
            return False
        resp = requests.delete(
            f"{RENDER_API}/services/{deploy_id}",
            headers=_headers(self.api_key),
            timeout=15,
        )
        return resp.ok
