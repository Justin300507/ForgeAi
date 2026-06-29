"""
Railway deployment provider.

Auth: reads token from ~/.railway/config.json (set by `railway login`).
Flow:
  1. GraphQL API → create project + service → get IDs
  2. Write .railway/config.json in generated project dir (non-interactive link)
  3. railway up --detach
  4. Poll railway domain for live URL
"""
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import requests

from app.deployments.base_provider import BaseDeploymentProvider, DeploymentResult

RAILWAY_GQL = "https://backboard.railway.app/graphql/v2"
_DOMAIN_RE = re.compile(r"https?://[\w.-]+\.up\.railway\.app")
_PLAIN_RE  = re.compile(r"[\w-]+\.up\.railway\.app")

RAILWAY_CONFIG = Path.home() / ".railway" / "config.json"


def _read_token() -> str | None:
    """Return Railway auth token — checks RAILWAY_TOKEN env var first, then ~/.railway/config.json."""
    env_token = os.environ.get("RAILWAY_TOKEN")
    if env_token:
        return env_token
    try:
        data = json.loads(RAILWAY_CONFIG.read_text())
        user = data.get("user", {})
        return user.get("accessToken") or user.get("token") or data.get("token")
    except Exception:
        return None


class RailwayProvider(BaseDeploymentProvider):

    def __init__(self) -> None:
        self.token = _read_token()

    def _check(self) -> str | None:
        if not self.token:
            return "No Railway token — add your personal token in Deploy Settings (railway.app/account/tokens)"
        return None

    def _gql(self, query: str, variables: dict | None = None) -> dict:
        resp = requests.post(
            RAILWAY_GQL,
            json={"query": query, "variables": variables or {}},
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
            timeout=60,
        )
        resp.raise_for_status()
        body = resp.json()
        if "errors" in body:
            raise RuntimeError(f"Railway API error: {body['errors']}")
        return body["data"]

    def _gql_retry(self, query: str, variables: dict | None = None, retries: int = 3) -> dict:
        last_err = None
        for attempt in range(retries):
            try:
                return self._gql(query, variables)
            except Exception as e:
                last_err = e
                err_str = str(e)
                # Railway rate-limits project creation to 1 per 30s — wait and retry
                if "too quickly" in err_str or "per 30 seconds" in err_str:
                    wait = 35
                    print(f"  [Railway] Rate limited — waiting {wait}s before retry ({attempt+1}/{retries})...")
                    time.sleep(wait)
                else:
                    time.sleep(3)
        raise last_err

    def _default_ids(self) -> tuple[str | None, str | None]:
        """Read linked project+service+environment IDs from ~/.railway/config.json."""
        try:
            data = json.loads(RAILWAY_CONFIG.read_text())
            for proj in data.get("projects", {}).values():
                pid = proj.get("project")
                sid = proj.get("service")
                if pid:
                    return pid, sid
        except Exception:
            pass
        return None, None

    def _default_environment_id(self) -> str | None:
        if getattr(self, "_cached_env_id", None):
            return self._cached_env_id
        try:
            data = json.loads(RAILWAY_CONFIG.read_text())
            for proj in data.get("projects", {}).values():
                eid = proj.get("environment")
                if eid:
                    return eid
        except Exception:
            pass
        return None

    def _write_local_railway_config(
        self,
        project_dir: Path,
        project_id: str,
        service_id: str,
    ) -> None:
        """
        Write a .railway/config.json in the generated project directory so the
        Railway CLI knows which project/service to deploy to without needing
        an interactive `railway link` step.
        """
        env_id = self._default_environment_id() or ""
        config = {
            "projects": {
                str(project_dir): {
                    "projectPath": str(project_dir),
                    "name": project_dir.name,
                    "project": project_id,
                    "environment": env_id,
                    "environmentName": "production",
                    "service": service_id,
                }
            }
        }
        railway_dir = project_dir / ".railway"
        railway_dir.mkdir(exist_ok=True)
        (railway_dir / "config.json").write_text(json.dumps(config, indent=2))

    def _link_globally(self, project_dir: Path, project_id: str, service_id: str) -> None:
        """
        Temporarily register this generated project path in ~/.railway/config.json.
        This is exactly what `railway link` does — it adds the current dir to the
        global projects map so the CLI knows which service to upload to.
        """
        try:
            data = json.loads(RAILWAY_CONFIG.read_text())
        except Exception:
            data = {"projects": {}, "user": {}}
        env_id = self._default_environment_id() or ""
        data.setdefault("projects", {})[str(project_dir)] = {
            "projectPath": str(project_dir),
            "name": project_dir.name,
            "project": project_id,
            "environment": env_id,
            "environmentName": "production",
            "service": service_id,
        }
        RAILWAY_CONFIG.write_text(json.dumps(data, indent=2))
        print(f"  [Railway] Registered {project_dir.name} in global Railway config")

    def _unlink_globally(self, project_dir: Path) -> None:
        """Remove the generated project entry from ~/.railway/config.json after deploy."""
        try:
            data = json.loads(RAILWAY_CONFIG.read_text())
            data.get("projects", {}).pop(str(project_dir), None)
            RAILWAY_CONFIG.write_text(json.dumps(data, indent=2))
        except Exception:
            pass

    def _create_service(self, project_id: str, name: str) -> str:
        """Create a new service in an existing project. Returns service_id."""
        svc = self._gql_retry(
            "mutation C($p: String!, $n: String!) "
            "{ serviceCreate(input: { projectId: $p, name: $n }) { id } }",
            {"p": project_id, "n": name},
        )
        return svc["serviceCreate"]["id"]

    def _create_project(self, name: str) -> tuple[str, str]:
        """
        Get or create a Railway project + service.
        If a project is already linked via ~/.railway/config.json, reuse it.
        Otherwise create a fresh project via the GraphQL API (works with personal tokens).
        Returns (project_id, service_id).
        """
        project_id, _ = self._default_ids()
        if not project_id:
            print(f"  [Railway] Creating new project '{name}'...")
            data = self._gql_retry(
                """mutation C($n: String!) {
                    projectCreate(input: { name: $n, defaultEnvironmentName: "production" }) {
                        id
                        environments { edges { node { id name } } }
                    }
                }""",
                {"n": name},
            )
            proj = data["projectCreate"]
            project_id = proj["id"]
            for edge in proj.get("environments", {}).get("edges", []):
                node = edge.get("node", {})
                if node.get("name") == "production":
                    self._cached_env_id = node["id"]
                    break
            print(f"  [Railway] Created project: {project_id}")
        else:
            print(f"  [Railway] Using existing project {project_id}")
        service_id = self._create_service(project_id, name)
        return project_id, service_id

    def _run(self, cmd: list[str], cwd: Path, timeout: int = 300) -> subprocess.CompletedProcess:
        env = {**os.environ}
        if self.token:
            env["RAILWAY_TOKEN"] = self.token
        return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)

    def _poll_domain(
        self, project_dir: Path, project_id: str, env: dict,
        attempts: int = 18, wait: int = 10,
    ) -> str | None:
        railway_exe = shutil.which("railway") or "railway"
        for i in range(attempts):
            time.sleep(wait)
            r = subprocess.run(
                [railway_exe, "domain"],
                cwd=project_dir, env=env, capture_output=True, text=True, timeout=15,
            )
            combined = r.stdout + r.stderr
            m = _DOMAIN_RE.search(combined) or _PLAIN_RE.search(combined)
            if m:
                raw = m.group(0)
                return raw if raw.startswith("http") else f"https://{raw}"
            print(f"  [Railway] No domain yet (attempt {i+1}/{attempts})...")
        return None

    def _connect_github(self, service_id: str, github_url: str, logs: list) -> None:
        """Connect a Railway service to a GitHub repo so Railway auto-deploys on push."""
        m = re.search(r'github\.com[:/]([^/]+/[^/.]+?)(?:\.git)?$', github_url)
        if not m:
            logs.append(f"[Railway] Could not parse GitHub URL: {github_url}")
            return
        repo = m.group(1)
        try:
            self._gql_retry(
                """mutation C($id: String!, $repo: String!) {
                    serviceConnect(id: $id, input: { repo: $repo, branch: "main" }) { id }
                }""",
                {"id": service_id, "repo": repo},
            )
            logs.append(f"[Railway] Connected service to GitHub: {repo}")
            print(f"  [Railway] Connected to GitHub: {repo}")
        except Exception as exc:
            logs.append(f"[Railway] GitHub connect skipped: {exc}")

    def _create_domain(self, service_id: str, environment_id: str, logs: list) -> str | None:
        """Create a Railway-provided domain for the service."""
        try:
            data = self._gql_retry(
                """mutation C($sid: String!, $eid: String!) {
                    serviceDomainCreate(input: { serviceId: $sid, environmentId: $eid }) { domain }
                }""",
                {"sid": service_id, "eid": environment_id},
            )
            domain = (data.get("serviceDomainCreate") or {}).get("domain")
            if domain:
                url = domain if domain.startswith("http") else f"https://{domain}"
                logs.append(f"[Railway] Domain: {url}")
                print(f"  [Railway] Domain assigned: {url}")
                return url
        except Exception as exc:
            logs.append(f"[Railway] Domain creation failed: {exc}")
        return None

    # ── public interface ──────────────────────────────────────────────────

    def deploy(
        self,
        project_path: str,
        project_name: str,
        env_vars: dict | None = None,
        github_url: str | None = None,
    ) -> DeploymentResult:
        """
        API-only deploy — no Railway CLI required.
        1. Create project + service via GraphQL
        2. Connect service to GitHub repo (triggers auto-deploy on Railway's end)
        3. Create a public domain
        4. Return the domain URL immediately (build runs async on Railway)
        """
        err = self._check()
        if err:
            return DeploymentResult(
                success=False, url=None, logs="", error=err,
                deploy_id=None, provider="railway",
            )

        logs: list[str] = []
        try:
            # ── Step 1: Create project + service ─────────────────────────
            service_name = f"{project_name[:40]}"
            print(f"  [Railway] Creating project + service '{service_name}'...")
            project_id, service_id = self._create_project(service_name)
            environment_id = self._default_environment_id() or ""
            logs.append(f"[Railway] project={project_id} service={service_id} env={environment_id}")

            # ── Step 2: Connect to GitHub so Railway auto-deploys ─────────
            if github_url:
                self._connect_github(service_id, github_url, logs)
            else:
                logs.append("[Railway] No GitHub URL — service created but not linked to a repo")

            # ── Step 3: Create a public domain ────────────────────────────
            url = self._create_domain(service_id, environment_id, logs)

            return DeploymentResult(
                success=bool(url),
                url=url,
                logs="\n".join(logs),
                error=None if url else "Service created — Railway will build from GitHub (check dashboard)",
                deploy_id=service_id,
                provider="railway",
                metadata={"project_id": project_id, "service_id": service_id},
            )

        except Exception as exc:
            return DeploymentResult(
                success=False, url=None, logs="\n".join(logs),
                error=str(exc), deploy_id=None, provider="railway",
            )

    def get_logs(self, deploy_id: str) -> str:
        try:
            r = subprocess.run(
                ["railway", "logs"], capture_output=True, text=True, timeout=30,
            )
            return r.stdout or r.stderr
        except Exception as exc:
            return str(exc)

    def get_status(self, deploy_id: str) -> str:
        try:
            r = subprocess.run(
                ["railway", "status"], capture_output=True, text=True, timeout=15,
            )
            return r.stdout or "unknown"
        except Exception:
            return "unknown"

    def delete(self, deploy_id: str) -> bool:
        return False
