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
from pathlib import Path

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
        return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=300, env=env)

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

        deploy_env = {
            **os.environ,
            "CLOUDFLARE_API_TOKEN": self.api_token,
            "CLOUDFLARE_ACCOUNT_ID": self.account_id,
        }
        if env_vars:
            deploy_env.update(env_vars)

        # Ensure node_modules exists
        package_json = Path(project_path) / "package.json"
        if not package_json.exists():
            return DeploymentResult(
                success=False, url=None, logs="",
                error="No package.json found — frontend not generated",
                deploy_id=None, provider="cloudflare",
            )

        # Build frontend
        print(f"  [Cloudflare] Building frontend...")
        build_result = self._run(["npm", "ci"], cwd, deploy_env)
        logs.append(f"[npm ci]\n{build_result.stdout[-500:]}\n{build_result.stderr[-200:]}")

        if build_result.returncode != 0:
            build_result = self._run(["npm", "install"], cwd, deploy_env)
            logs.append(f"[npm install fallback]\n{build_result.stderr[-200:]}")

        build_result = self._run(["npm", "run", "build"], cwd, deploy_env)
        logs.append(f"[npm build]\n{build_result.stdout[-500:]}\n{build_result.stderr[-300:]}")

        if build_result.returncode != 0:
            return DeploymentResult(
                success=False, url=None, logs="\n".join(logs),
                error=f"Frontend build failed: {build_result.stderr[-400:]}",
                deploy_id=None, provider="cloudflare",
            )

        dist_dir = Path(project_path) / "dist"
        if not dist_dir.exists():
            return DeploymentResult(
                success=False, url=None, logs="\n".join(logs),
                error="dist/ not found after build",
                deploy_id=None, provider="cloudflare",
            )

        # Deploy to Cloudflare Pages
        print(f"  [Cloudflare] Deploying {slug} to Pages...")
        wrangler = shutil.which("wrangler") or "wrangler"
        deploy_result = self._run(
            [wrangler, "pages", "deploy", "./dist", f"--project-name={slug}", "--branch=main"],
            cwd, deploy_env,
        )
        combined = deploy_result.stdout + deploy_result.stderr
        logs.append(f"[wrangler deploy]\n{combined[-1000:]}")

        # Extract URL from output
        url_match = _URL_RE.search(combined)
        url = url_match.group(0) if url_match else None

        if deploy_result.returncode != 0 and not url:
            return DeploymentResult(
                success=False, url=None, logs="\n".join(logs),
                error=f"Wrangler deploy failed: {deploy_result.stderr[-400:]}",
                deploy_id=None, provider="cloudflare",
            )

        print(f"  [Cloudflare] Frontend live at: {url}")
        return DeploymentResult(
            success=True,
            url=url or f"https://{slug}.pages.dev",
            logs="\n".join(logs),
            error=None,
            deploy_id=slug,
            provider="cloudflare",
        )

    def get_logs(self, deploy_id: str) -> str:
        return "Use the Cloudflare dashboard to view deployment logs."

    def get_status(self, deploy_id: str) -> str:
        return "deployed"

    def delete(self, deploy_id: str) -> bool:
        return False
