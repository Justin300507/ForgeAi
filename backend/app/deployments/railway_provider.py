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
    """Read auth token from ~/.railway/config.json (written by `railway login`)."""
    try:
        data = json.loads(RAILWAY_CONFIG.read_text())
        user = data.get("user", {})
        # Prefer accessToken (OAuth flow), fall back to legacy token field
        return user.get("accessToken") or user.get("token") or data.get("token")
    except Exception:
        return None


class RailwayProvider(BaseDeploymentProvider):

    def __init__(self) -> None:
        self.token = _read_token()

    def _check(self) -> str | None:
        if not shutil.which("railway"):
            return "Railway CLI not found — install with: npm install -g @railway/cli"
        if not self.token:
            return (
                "Not logged in to Railway — run: railway login\n"
                f"(expected token in {RAILWAY_CONFIG})"
            )
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
        for _ in range(retries):
            try:
                return self._gql(query, variables)
            except Exception as e:
                last_err = e
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
        Strategy: reuse the existing linked project (from railway link) and
        create a new service in it — avoids the slow projectCreate API call.
        Returns (project_id, service_id).
        """
        project_id, _ = self._default_ids()
        if not project_id:
            raise RuntimeError(
                "No linked Railway project found.\n"
                "Run: railway link  — to link a project, then retry."
            )
        print(f"  [Railway] Using existing project {project_id}")
        service_id = self._create_service(project_id, name)
        return project_id, service_id

    def _run(self, cmd: list[str], cwd: Path, timeout: int = 300) -> subprocess.CompletedProcess:
        return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)

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

    # ── public interface ──────────────────────────────────────────────────

    def deploy(
        self,
        project_path: str,
        project_name: str,
        env_vars: dict | None = None,
    ) -> DeploymentResult:
        err = self._check()
        if err:
            return DeploymentResult(
                success=False, url=None, logs="", error=err,
                deploy_id=None, provider="railway",
            )

        project_dir = Path(project_path)
        logs: list[str] = []

        try:
            # ── Step 1: Get existing project ID + build env ──────────────
            project_id, _ = self._default_ids()
            if not project_id:
                return DeploymentResult(
                    success=False, url=None, logs="",
                    error="No linked Railway project. Run: railway link",
                    deploy_id=None, provider="railway",
                )

            railway_exe = shutil.which("railway") or "railway"
            environment_id = self._default_environment_id() or ""
            # Do NOT pass RAILWAY_TOKEN — that's for project tokens, not user OAuth tokens.
            # Auth comes from ~/.railway/config.json; we link the generated project there.
            env = {
                **os.environ,
                "RAILWAY_PROJECT_ID": project_id,
                "RAILWAY_ENVIRONMENT_ID": environment_id,
            }
            if env_vars:
                env.update(env_vars)

            # ── Step 2: Create a fresh service via GraphQL ────────────────
            # Append timestamp to avoid name collisions on re-runs of the same project
            service_name = f"{project_name[:40]}_{int(time.time())}"
            print(f"  [Railway] Creating service '{service_name}'...")
            try:
                service_id: str | None = self._create_service(project_id, service_name)
                print(f"  [Railway] Service created: {service_id}")
            except Exception as exc:
                print(f"  [Railway] Service creation via API failed ({exc}) — falling back to railway add")
                add = subprocess.run(
                    [railway_exe, "add", "--service", project_name, "--json"],
                    cwd=project_dir, env=env,
                    capture_output=True, text=True, timeout=60,
                )
                logs.append(f"[add]\n{add.stdout}\n{add.stderr}")
                service_id = None
                try:
                    service_id = json.loads(add.stdout).get("serviceId") or json.loads(add.stdout).get("id")
                except Exception:
                    pass
                if not service_id:
                    m = re.search(r'[0-9a-f]{8}-[0-9a-f-]{27}', add.stdout + add.stderr)
                    service_id = m.group(0) if m else None

            if service_id:
                env["RAILWAY_SERVICE_ID"] = service_id
                # Register in global Railway config — this is exactly what `railway link` does.
                # This lets `railway up` from the generated project dir find the right service
                # without the user running `railway link` interactively.
                self._link_globally(project_dir, project_id, service_id)
                print(f"  [Railway] Global config linked for service {service_id}")
            else:
                print(f"  [Railway] Service ID unknown — deploying without service binding")

            # ── Step 3: Deploy to the new service ────────────────────────
            print(f"  [Railway] Uploading {project_dir.name}...")
            try:
                up = subprocess.run(
                    [railway_exe, "up", "--detach"],
                    cwd=project_dir, env=env,
                    capture_output=True, text=True, timeout=300,
                )
            finally:
                # Always clean up the global config entry after deploy attempt
                if service_id:
                    self._unlink_globally(project_dir)

            logs.append(f"[up stdout]\n{up.stdout}\n[up stderr]\n{up.stderr}")

            if up.returncode != 0:
                return DeploymentResult(
                    success=False, url=None,
                    logs="\n".join(logs)[:3000],
                    error=f"railway up failed (exit {up.returncode}): {up.stderr[:400]}",
                    deploy_id=service_id or project_name, provider="railway",
                    metadata={"project_id": project_id},
                )

            # ── Step 5: Get domain ────────────────────────────────────────
            print("  [Railway] Waiting for domain...")
            url = self._poll_domain(project_dir, project_id, env)

            return DeploymentResult(
                success=bool(url),
                url=url,
                logs="\n".join(logs)[:3000],
                error=None if url else "No domain yet — check railway.app dashboard",
                deploy_id=project_name,
                provider="railway",
                metadata={"project_id": project_id},
            )

        except subprocess.TimeoutExpired:
            return DeploymentResult(
                success=False, url=None, logs="\n".join(logs),
                error="Railway deployment timed out",
                deploy_id=None, provider="railway",
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
