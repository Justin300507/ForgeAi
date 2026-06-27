"""
V14 Deployment Config Service

Generates all deployment configuration files deterministically (no LLM needed):
  - render.yaml        — Render.com blueprint (backend web service + frontend static site)
  - Procfile           — Heroku-compatible process file (also used by Render)
  - .env.example       — Template of all required environment variables
  - .github/workflows/deploy.yml  — GitHub Actions CI/CD pipeline
  - wrangler.toml      — Cloudflare Pages config for the frontend

These replace LLM-generated versions which are often wrong or inconsistent.
"""
import re
from pathlib import Path


# ── Helpers ──────────────────────────────────────────────────────────────────

def _extract_env_vars_from_routes(project_path: str) -> list[str]:
    """Scan Python files for os.getenv / os.environ calls to find required env vars."""
    needed = set()
    for py_file in Path(project_path).rglob("*.py"):
        try:
            text = py_file.read_text(encoding="utf-8", errors="ignore")
            needed.update(re.findall(r'os\.getenv\(["\']([A-Z_]+)["\']', text))
            needed.update(re.findall(r'os\.environ\.get\(["\']([A-Z_]+)["\']', text))
            needed.update(re.findall(r'os\.environ\[["\']([A-Z_]+)["\']', text))
        except Exception:
            continue
    # Always include these regardless
    needed.update({"SECRET_KEY", "DATABASE_URL"})
    return sorted(needed)


def _project_slug(project_name: str) -> str:
    return project_name.lower().replace("_", "-").replace(" ", "-")


# ── render.yaml ──────────────────────────────────────────────────────────────

def _build_render_yaml(project_name: str, env_vars: list[str]) -> str:
    slug = _project_slug(project_name)
    env_block = ""
    for var in env_vars:
        if var == "SECRET_KEY":
            env_block += f"      - key: SECRET_KEY\n        generateValue: true\n"
        elif var == "DATABASE_URL":
            env_block += f"      - key: DATABASE_URL\n        fromDatabase:\n          name: {slug}-db\n          property: connectionString\n"
        else:
            env_block += f"      - key: {var}\n        sync: false\n"

    return f"""databases:
  - name: {slug}-db
    databaseName: {slug.replace('-', '_')}_db
    plan: free

services:
  - type: web
    name: {slug}-backend
    runtime: python
    plan: free
    buildCommand: pip install -r app/requirements.txt
    startCommand: uvicorn app.main:app --host 0.0.0.0 --port $PORT
    healthCheckPath: /health
    envVars:
{env_block}
  - type: web
    name: {slug}-frontend
    runtime: static
    buildCommand: npm ci && npm run build
    staticPublishPath: ./dist
    pullRequestPreviewsEnabled: true
    routes:
      - type: rewrite
        source: /*
        destination: /index.html
    envVars:
      - key: VITE_API_URL
        sync: false
"""


# ── Procfile ─────────────────────────────────────────────────────────────────

def _build_procfile() -> str:
    return "web: uvicorn app.main:app --host 0.0.0.0 --port $PORT\n"


# ── .env.example ─────────────────────────────────────────────────────────────

_ENV_DESCRIPTIONS = {
    "SECRET_KEY": "JWT signing secret — generate with: python -c \"import secrets; print(secrets.token_hex(32))\"",
    "DATABASE_URL": "PostgreSQL connection string — e.g. postgresql://user:password@host:5432/dbname (leave empty to use SQLite)",
    "VITE_API_URL": "Backend API URL for the frontend — e.g. https://your-backend.onrender.com",
    "CORS_ORIGINS": "Comma-separated list of allowed frontend origins — e.g. https://your-frontend.pages.dev",
    "DEBUG": "Set to 'true' for development debugging",
    "PORT": "Server port (default: 8000)",
}

def _build_env_example(env_vars: list[str]) -> str:
    lines = ["# .env.example — copy to .env and fill in your values\n"]
    for var in env_vars:
        desc = _ENV_DESCRIPTIONS.get(var, f"Set your {var}")
        lines.append(f"# {desc}")
        lines.append(f"{var}=\n")
    # Always add VITE_API_URL for the frontend
    if "VITE_API_URL" not in env_vars:
        lines.append("# Backend API URL for the React frontend")
        lines.append("VITE_API_URL=http://localhost:8000\n")
    return "\n".join(lines)


# ── GitHub Actions ────────────────────────────────────────────────────────────

def _build_github_actions(project_name: str) -> str:
    slug = _project_slug(project_name)
    return f"""name: Deploy {project_name.replace('_', ' ').title()}

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  test-backend:
    name: Backend Tests
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'
          cache: pip
      - name: Install dependencies
        run: pip install -r app/requirements.txt
      - name: Check syntax
        run: python -m py_compile app/main.py
      - name: Start backend (smoke test)
        run: |
          uvicorn app.main:app --host 0.0.0.0 --port 8000 &
          sleep 5
          curl -f http://localhost:8000/health || exit 1
        env:
          SECRET_KEY: ci-test-secret-key-not-for-production

  build-frontend:
    name: Build Frontend
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Set up Node
        uses: actions/setup-node@v4
        with:
          node-version: '20'
          cache: npm
      - name: Install dependencies
        run: npm ci
      - name: Build
        run: npm run build
        env:
          VITE_API_URL: ${{{{ secrets.VITE_API_URL || 'http://localhost:8000' }}}}

  deploy-frontend:
    name: Deploy to Cloudflare Pages
    needs: build-frontend
    runs-on: ubuntu-latest
    if: github.ref == 'refs/heads/main'
    steps:
      - uses: actions/checkout@v4
      - name: Set up Node
        uses: actions/setup-node@v4
        with:
          node-version: '20'
          cache: npm
      - name: Install and build
        run: npm ci && npm run build
        env:
          VITE_API_URL: ${{{{ secrets.VITE_API_URL }}}}
      - name: Deploy to Cloudflare Pages
        uses: cloudflare/wrangler-action@v3
        with:
          apiToken: ${{{{ secrets.CLOUDFLARE_API_TOKEN }}}}
          accountId: ${{{{ secrets.CLOUDFLARE_ACCOUNT_ID }}}}
          command: pages deploy dist --project-name={slug}-frontend
"""


# ── wrangler.toml ─────────────────────────────────────────────────────────────

def _build_wrangler_toml(project_name: str) -> str:
    slug = _project_slug(project_name)
    return f"""name = "{slug}-frontend"
compatibility_date = "2024-01-01"

[build]
command = "npm ci && npm run build"
cwd = "."

[build.upload]
format = "directory"
dir = "./dist"

[env.production]
name = "{slug}-frontend"
"""


# ── Main entry point ──────────────────────────────────────────────────────────

def generate_deployment_configs(project_path: str, project_name: str) -> dict:
    """
    Write all deployment config files into the project directory.
    Returns a dict of {filename: "written" | "error"}.
    """
    root = Path(project_path)
    env_vars = _extract_env_vars_from_routes(project_path)

    files = {
        "render.yaml": _build_render_yaml(project_name, env_vars),
        "Procfile": _build_procfile(),
        ".env.example": _build_env_example(env_vars),
        ".github/workflows/deploy.yml": _build_github_actions(project_name),
        "wrangler.toml": _build_wrangler_toml(project_name),
    }

    results = {}
    for rel_path, content in files.items():
        try:
            out = root / rel_path
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(content, encoding="utf-8")
            results[rel_path] = "written"
        except Exception as e:
            results[rel_path] = f"error: {e}"

    print(f"\n[V14] Deployment configs written: {list(results.keys())}")
    return results
