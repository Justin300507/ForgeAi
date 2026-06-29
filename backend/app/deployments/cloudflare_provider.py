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
import tempfile
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

    def _prepare_build_dir(self, project_path: str, slug: str) -> Path:
        """Copy frontend source files to /tmp for isolated npm build.

        Builds in /tmp instead of in-place to avoid ENOENT errors on constrained
        filesystems (Railway /data volumes, Docker read-only layers, etc.).
        Excludes the Python backend and any existing build artifacts.
        """
        _EXCLUDE = {"app", "node_modules", "dist", ".git", ".env", "venv", ".venv", "__pycache__"}
        tmp = Path(tempfile.mkdtemp(prefix=f"forge-cf-{slug[:20]}-"))
        for item in Path(project_path).iterdir():
            if item.name in _EXCLUDE:
                continue
            dst = tmp / item.name
            try:
                if item.is_dir():
                    shutil.copytree(
                        str(item), str(dst),
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
                    )
                else:
                    shutil.copy2(str(item), str(dst))
            except Exception as copy_err:
                print(f"  [Cloudflare] Warning: skipped {item.name}: {copy_err}")
        return tmp

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
        logs: list[str] = []
        build_dir: Path | None = None

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
        # npm cache in /tmp avoids permission failures on restricted filesystems
        npm_cache = str(Path(tempfile.gettempdir()) / ".npm-forge-cache")
        # Explicitly unset CI so Vite doesn't treat warnings as fatal errors.
        # Railway (and most CI platforms) set CI=true in the process environment,
        # which would be inherited by {**os.environ} and break the build.
        build_env = {k: v for k, v in os.environ.items() if k != "CI"}
        build_env["npm_config_cache"] = npm_cache
        if env_vars:
            wrangler_env.update(env_vars)
            build_env.update(env_vars)

        package_json = Path(project_path) / "package.json"
        if not package_json.exists():
            return DeploymentResult(
                success=False, url=None, logs="",
                error="No package.json found — frontend not generated",
                deploy_id=None, provider="cloudflare",
            )

        npm = _npm()
        if npm == "npm" and not shutil.which("npm"):
            return DeploymentResult(
                success=False, url=None, logs="",
                error="npm not found — install Node.js to enable frontend deployment",
                deploy_id=None, provider="cloudflare",
            )

        try:
            # ── Pre-build disk cleanup ───────────────────────────────────────
            # Accumulated node_modules and npm caches on the /data volume cause
            # ENOSPC failures. Remove leftovers from previous builds before starting.
            import glob as _glob
            for _old in _glob.glob("/tmp/forge-cf-*"):
                shutil.rmtree(_old, ignore_errors=True)
            # Remove node_modules from previously-generated projects to free /data
            for _nm in _glob.glob("/data/generated_projects/*/node_modules"):
                shutil.rmtree(_nm, ignore_errors=True)
            # Clear the npm cache so it doesn't eat temp space
            try:
                subprocess.run([npm, "cache", "clean", "--force"], capture_output=True, timeout=60)
            except Exception:
                pass

            # Build in /tmp to avoid ENOENT errors on Railway /data volumes and other
            # constrained filesystems where npm can't create node_modules in-place.
            build_dir = self._prepare_build_dir(project_path, slug)
            cwd = str(build_dir)

            print(f"  [Cloudflare] Source:       {project_path}")
            print(f"  [Cloudflare] Build dir:    {build_dir}")
            print(f"  [Cloudflare] package.json: {'✓' if (build_dir / 'package.json').exists() else '✗ MISSING'}")
            print(f"  [Cloudflare] src/:         {'✓' if (build_dir / 'src').exists() else '✗ MISSING'}")

            # Patch generated api.js/api.jsx BEFORE the build so the Render backend
            # URL (VITE_API_URL) is actually used. LLMs sometimes generate baseURL:''
            # which works on same-domain deploys but breaks Cloudflare+Render setups.
            for api_file in list(build_dir.rglob("src/api.js")) + list(build_dir.rglob("src/api.jsx")):
                try:
                    text = api_file.read_text(encoding="utf-8")
                    patched = re.sub(
                        r"baseURL\s*:\s*['\"]['\"]",
                        "baseURL: import.meta.env.VITE_API_URL || ''",
                        text,
                    )
                    if patched != text:
                        api_file.write_text(patched, encoding="utf-8")
                        print(f"  [Cloudflare] Patched {api_file.name}: baseURL '' → VITE_API_URL")
                except Exception as patch_err:
                    print(f"  [Cloudflare] Warning: could not patch {api_file}: {patch_err}")

            print(f"  [Cloudflare] Running npm install (npm={npm})...")
            install_result = self._run(
                [npm, "install", "--no-fund", "--no-audit", "--legacy-peer-deps"],
                cwd, build_env,
            )
            logs.append(f"[npm install]\nstdout:\n{install_result.stdout}\nstderr:\n{install_result.stderr}")

            if install_result.returncode != 0:
                diag = (
                    f"Build dir: {build_dir}\n"
                    f"package.json: {(build_dir / 'package.json').exists()}\n"
                    f"src/: {(build_dir / 'src').exists()}\n\n"
                    f"stdout:\n{install_result.stdout}\n"
                    f"stderr:\n{install_result.stderr}"
                )
                print(f"  [Cloudflare] npm install FAILED (rc={install_result.returncode})")
                print(diag[:800])
                return DeploymentResult(
                    success=False, url=None, logs="\n".join(logs),
                    error=f"npm install failed (rc={install_result.returncode}):\n{diag}",
                    deploy_id=None, provider="cloudflare",
                )

            build_result = self._run([npm, "run", "build"], cwd, build_env)
            logs.append(f"[npm build]\n{build_result.stdout[-500:]}\n{build_result.stderr[-300:]}")

            if build_result.returncode != 0:
                # Vite writes errors to stdout; stderr has the JS stack trace.
                # Combine both, but put stdout first so the actual error is visible.
                combined_err = (
                    (build_result.stdout or "").strip()
                    + "\n"
                    + (build_result.stderr or "").strip()
                ).strip()
                # Show the first 800 chars (actual error) plus last 400 (tail)
                if len(combined_err) > 1200:
                    build_err = combined_err[:800] + "\n...\n" + combined_err[-400:]
                else:
                    build_err = combined_err
                print(f"  [Cloudflare] Frontend build failed (rc={build_result.returncode}):")
                print(f"  {build_err[:600]}")
                return DeploymentResult(
                    success=False, url=None, logs="\n".join(logs),
                    error=f"Frontend build failed: {build_err}",
                    deploy_id=None, provider="cloudflare",
                )

            dist_dir = build_dir / "dist"
            if not dist_dir.exists():
                return DeploymentResult(
                    success=False, url=None, logs="\n".join(logs),
                    error="dist/ not found after build",
                    deploy_id=None, provider="cloudflare",
                )

            # SPA routing: serve index.html for all unmatched paths so
            # React Router handles /login, /dashboard, etc. without 404
            (dist_dir / "_redirects").write_text("/* /index.html 200\n")

            # Ensure the Cloudflare Pages project exists (wrangler won't auto-create in CI mode)
            self._ensure_project_exists(slug, logs)

            print(f"  [Cloudflare] Deploying {slug} to Pages...")
            wrangler = shutil.which("wrangler") or "wrangler"
            deploy_result = self._run(
                [wrangler, "pages", "deploy", "--project-name", slug, "--branch", "main", "--directory", "dist"],
                cwd, wrangler_env,
            )
            combined = (deploy_result.stdout or "") + (deploy_result.stderr or "")
            logs.append(f"[wrangler deploy]\n{combined[-1000:]}")

            url_match = _URL_RE.search(combined)
            url = url_match.group(0) if url_match else None

            if deploy_result.returncode != 0:
                error_tail = (deploy_result.stderr or "")[-400:]
                print(f"  [Cloudflare] Deploy failed — {error_tail[:120]}")
                return DeploymentResult(
                    success=False, url=None, logs="\n".join(logs),
                    error=error_tail, deploy_id=None, provider="cloudflare",
                )

            final_url = url or f"https://{slug}.pages.dev"
            print(f"  [Cloudflare] Frontend live at: {final_url}")
            return DeploymentResult(
                success=True, url=final_url, logs="\n".join(logs),
                error=None, deploy_id=slug, provider="cloudflare",
            )

        finally:
            # Always clean up the temp build dir
            if build_dir and build_dir.exists():
                shutil.rmtree(str(build_dir), ignore_errors=True)

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
