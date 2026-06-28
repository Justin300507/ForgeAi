"""
Cloudflare Pages deployment provider — uses Wrangler CLI.

Requires:
  - Wrangler CLI installed: npm install -g wrangler
  - CLOUDFLARE_API_TOKEN in backend/.env
  - CLOUDFLARE_ACCOUNT_ID in backend/.env

Flow:
  1. npm ci && npm run build  (build the React frontend)
  2. wrangler pages deploy ./dist --project-name=<slug>  (deploy to Cloudflare Pages)
  3. Parse the deployment URL from wrangler output

The project is created automatically on first deploy if it doesn't exist.
"""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

def _npm() -> str:
    found = shutil.which("npm")
    if found:
        return found
    if sys.platform == "win32":
        for candidate in ["npm.cmd", "npm.bat"]:
            found = shutil.which(candidate)
            if found:
                return found
    return "npm"

from app.deployments.base_provider import BaseDeploymentProvider, DeploymentResult

_URL_RE = re.compile(r"https://[\w.-]+\.pages\.dev")


def _slug(name: str) -> str:
    return name.lower().replace("_", "-").replace(" ", "-")[:60]


class CloudflareProvider(BaseDeploymentProvider):
    """Deploy the frontend to Cloudflare Pages."""

    def __init__(self) -> None:
        self.api_token = os.getenv("CLOUDFLARE_API_TOKEN")
        self.account_id = os.getenv("CLOUDFLARE_ACCOUNT_ID")

    def _check(self) -> str | None:
        if not shutil.which("wrangler"):
            return "Wrangler CLI not found — install with: npm install -g wrangler"
        if not self.api_token:
            return "CLOUDFLARE_API_TOKEN not set in .env"
        if not self.account_id:
            return "CLOUDFLARE_ACCOUNT_ID not set in .env"
        return None

    def _run(self, cmd: list[str], cwd: str, env: dict) -> subprocess.CompletedProcess:
        result = subprocess.run(
            cmd, cwd=cwd, capture_output=True, timeout=300, env=env,
            input=b"y\n",  # auto-confirm any interactive "create project?" prompts
        )
        stdout = (result.stdout or b"").decode("utf-8", errors="replace")
        stderr = (result.stderr or b"").decode("utf-8", errors="replace")
        return subprocess.CompletedProcess(result.args, result.returncode, stdout, stderr)

    def deploy(
        self,
        project_path: str,
        project_name: str,
        env_vars: dict | None = None,
    ) -> DeploymentResult:
        err = self._check()
        if err:
            return DeploymentResult(success=False, url=None, logs="", error=err,
                                    deploy_id=None, provider="cloudflare")

        slug = _slug(project_name)
        cwd = str(project_path)
        logs: list[str] = []

        # wrangler-specific env — CI=true needed for wrangler but NOT for npm build
        # (CI=true makes Vite/CRA treat warnings as errors, breaking the build)
        wrangler_env = {
            **os.environ,
            "CLOUDFLARE_API_TOKEN": self.api_token,
            "CLOUDFLARE_ACCOUNT_ID": self.account_id,
            "CF_API_TOKEN": self.api_token,
            "WRANGLER_SEND_METRICS": "false",
            "CI": "true",
        }
        build_env = {**os.environ}  # no CI=true during npm build
        if env_vars:
            wrangler_env.update(env_vars)
            build_env.update(env_vars)

        # Ensure node_modules exists
        package_json = Path(project_path) / "package.json"
        if not package_json.exists():
            return DeploymentResult(
                success=False, url=None, logs="",
                error="No package.json found — frontend not generated",
                deploy_id=None, provider="cloudflare",
            )

        # Build frontend
        npm = _npm()
        print(f"  [Cloudflare] Building frontend (npm={npm})...")
        build_result = self._run([npm, "ci"], cwd, build_env)
        logs.append(f"[npm ci]\n{build_result.stdout[-500:]}\n{build_result.stderr[-200:]}")

        if build_result.returncode != 0:
            print(f"  [Cloudflare] npm ci failed (rc={build_result.returncode}), trying npm install...")
            build_result = self._run([npm, "install"], cwd, build_env)
            logs.append(f"[npm install fallback]\n{build_result.stderr[-200:]}")
            if build_result.returncode != 0:
                err = build_result.stderr[-400:]
                print(f"  [Cloudflare] npm install also failed — {err[:120]}")
                return DeploymentResult(
                    success=False, url=None, logs="\n".join(logs),
                    error=f"npm install failed: {err}",
                    deploy_id=None, provider="cloudflare",
                )

        build_result = self._run([npm, "run", "build"], cwd, build_env)
        logs.append(f"[npm build]\n{build_result.stdout[-500:]}\n{build_result.stderr[-300:]}")

        if build_result.returncode != 0:
            err = build_result.stderr[-600:] or build_result.stdout[-400:]
            print(f"  [Cloudflare] Frontend build failed — {err[:200]}")
            return DeploymentResult(
                success=False, url=None, logs="\n".join(logs),
                error=f"Frontend build failed: {err}",
                deploy_id=None, provider="cloudflare",
            )

        dist_dir = Path(project_path) / "dist"
        if not dist_dir.exists():
            return DeploymentResult(
                success=False, url=None, logs="\n".join(logs),
                error="dist/ not found after build",
                deploy_id=None, provider="cloudflare",
            )

        # Ensure the Cloudflare Pages project exists (wrangler won't auto-create in CI mode)
        self._ensure_project_exists(slug, logs)

        # Deploy to Cloudflare Pages
        print(f"  [Cloudflare] Deploying {slug} to Pages...")
        wrangler = shutil.which("wrangler") or "wrangler"
        deploy_result = self._run(
            [wrangler, "pages", "deploy", "--project-name", slug, "--branch", "main", "--directory", "dist"],
            cwd, wrangler_env,
        )
        combined = (deploy_result.stdout or "") + (deploy_result.stderr or "")
        logs.append(f"[wrangler deploy]\n{combined[-1000:]}")

        # Extract URL from output
        url_match = _URL_RE.search(combined)
        url = url_match.group(0) if url_match else None

        if deploy_result.returncode != 0:
            error_tail = (deploy_result.stderr or "")[-400:]
            print(f"  [Cloudflare] Deploy failed — {error_tail[:120]}")
            return DeploymentResult(
                success=False,
                url=None,
                logs="\n".join(logs),
                error=error_tail,
                deploy_id=None,
                provider="cloudflare",
            )

        # Only use fallback URL when wrangler actually succeeded (exit 0)
        final_url = url or f"https://{slug}.pages.dev"
        print(f"  [Cloudflare] Frontend live at: {final_url}")
        return DeploymentResult(
            success=True,
            url=final_url,
            logs="\n".join(logs),
            error=None,
            deploy_id=slug,
            provider="cloudflare",
        )

    def _ensure_project_exists(self, slug: str, logs: list) -> None:
        """Create the Cloudflare Pages project via REST API if it doesn't exist yet.
        Wrangler in CI mode (CI=true) will NOT auto-create projects, so we do it here."""
        try:
            import urllib.request
            import json as _json
            api_base = f"https://api.cloudflare.com/client/v4/accounts/{self.account_id}/pages/projects"
            headers = {
                "Authorization": f"Bearer {self.api_token}",
                "Content-Type": "application/json",
            }
            # Check if project exists
            req = urllib.request.Request(f"{api_base}/{slug}", headers=headers)
            try:
                urllib.request.urlopen(req, timeout=15)
                logs.append(f"[CF API] Project '{slug}' already exists")
                return  # exists — nothing to do
            except urllib.error.HTTPError as e:
                if e.code != 404:
                    logs.append(f"[CF API] Unexpected status {e.code} checking project")
                    return
            # 404 → create it
            body = _json.dumps({"name": slug, "production_branch": "main"}).encode()
            create_req = urllib.request.Request(api_base, data=body, headers=headers, method="POST")
            try:
                urllib.request.urlopen(create_req, timeout=15)
                print(f"  [Cloudflare] Created Pages project '{slug}'")
                logs.append(f"[CF API] Created project '{slug}'")
            except urllib.error.HTTPError as e:
                err = e.read().decode("utf-8", errors="replace")[:200]
                logs.append(f"[CF API] Could not create project: {e.code} {err}")
        except Exception as ex:
            logs.append(f"[CF API] _ensure_project_exists error: {ex}")

    def get_logs(self, deploy_id: str) -> str:
        return "Use the Cloudflare dashboard to view deployment logs."

    def get_status(self, deploy_id: str) -> str:
        return "deployed"

    def delete(self, deploy_id: str) -> bool:
        return False
