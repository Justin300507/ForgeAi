"""
V5.6 Repository Intelligence

Auto-generates portfolio-ready documentation for every generated project:
  - README.md with features, API reference, setup guide
  - API_DOCS.md with all endpoints
  - ARCHITECTURE.md with ER diagram (text-based) and folder structure
  - DEPLOYMENT.md with Docker + Railway + Render instructions

No LLM needed for most of it — pure template + metadata extraction.
The README summary paragraph uses a single LLM call for quality.
"""
import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class RepoDocsResult:
    files_written: list[str] = field(default_factory=list)
    skipped: bool = False
    skip_reason: str = ""


def _extract_endpoints(project_path: str) -> list[dict]:
    """Parse route files to extract endpoint info."""
    import re
    routes_dir = Path(project_path) / "app" / "routes"
    endpoints = []
    if not routes_dir.exists():
        return endpoints

    route_re = re.compile(
        r'@\w+\.(get|post|put|delete|patch)\(["\']([^"\']+)["\'].*?(?:response_model=(\w+))?',
        re.IGNORECASE
    )
    func_re = re.compile(r'def (\w+)\(')

    for f in sorted(routes_dir.glob("*.py")):
        if f.name == "__init__.py":
            continue
        try:
            content = f.read_text(encoding="utf-8")
            lines = content.splitlines()
            for i, line in enumerate(lines):
                m = route_re.search(line)
                if m:
                    method = m.group(1).upper()
                    path = m.group(2)
                    response = m.group(3) or ""
                    # Get the function name from next non-decorator line
                    description = ""
                    for j in range(i + 1, min(i + 5, len(lines))):
                        fm = func_re.search(lines[j])
                        if fm:
                            description = fm.group(1).replace("_", " ").title()
                            break
                    endpoints.append({
                        "method": method, "path": path,
                        "description": description, "response": response,
                        "file": f.name,
                    })
        except Exception:
            continue
    return endpoints


def _extract_models(project_path: str) -> list[dict]:
    """Extract SQLAlchemy models from models/ directory."""
    import re
    models_dir = Path(project_path) / "app" / "models"
    models = []
    if not models_dir.exists():
        return models

    class_re = re.compile(r'class (\w+)\(Base\)')
    col_re = re.compile(r'^\s+(\w+)\s*=\s*Column\(([^)]+)\)')

    for f in sorted(models_dir.glob("*.py")):
        if f.name == "__init__.py":
            continue
        try:
            content = f.read_text(encoding="utf-8")
            current_model = None
            columns = []
            for line in content.splitlines():
                cm = class_re.search(line)
                if cm:
                    if current_model:
                        models.append({"name": current_model, "columns": columns})
                    current_model = cm.group(1)
                    columns = []
                elif current_model:
                    colm = col_re.search(line)
                    if colm:
                        col_name = colm.group(1)
                        col_type = colm.group(2).split(",")[0].strip()
                        columns.append({"name": col_name, "type": col_type})
            if current_model:
                models.append({"name": current_model, "columns": columns})
        except Exception:
            continue
    return models


def _build_er_diagram(models: list[dict]) -> str:
    """Build a simple text-based ER diagram."""
    if not models:
        return "_No models detected._"
    lines = ["```"]
    for m in models:
        lines.append(f"┌─────────────────────┐")
        lines.append(f"│ {m['name']:19s} │")
        lines.append(f"├─────────────────────┤")
        for col in m["columns"][:8]:
            line = f"│ {col['name']:10s} {col['type']:8s}│"
            lines.append(line[:23] + "│")
        if len(m["columns"]) > 8:
            lines.append(f"│ ... ({len(m['columns'])-8} more)    │")
        lines.append(f"└─────────────────────┘")
        lines.append("")
    lines.append("```")
    return "\n".join(lines)


def _build_readme(
    project_name: str,
    plan: dict,
    architecture: dict,
    endpoints: list[dict],
    models: list[dict],
) -> str:
    # Defensive: plan or architecture may be None or list in edge cases
    if not isinstance(plan, dict):
        plan = {}
    if not isinstance(architecture, dict):
        architecture = {}

    display_name = project_name.replace("_", " ").title()
    features = plan.get("features", [])
    tech_stack = plan.get("tech_stack", {})
    # tech_stack may be a list (["FastAPI", "React", "SQLite"]) or a dict
    if isinstance(tech_stack, list):
        backend = next((t for t in tech_stack if "fast" in t.lower() or "django" in t.lower() or "flask" in t.lower()), "FastAPI")
        db = next((t for t in tech_stack if any(x in t.lower() for x in ("sqlite", "postgres", "mysql", "mongo"))), "SQLite")
    else:
        backend = tech_stack.get("backend", "FastAPI")
        db = tech_stack.get("database", "SQLite")

    features_md = "\n".join(f"- {f}" for f in features[:10]) if features else "- Full CRUD operations\n- User authentication\n- REST API"

    endpoint_table = "| Method | Path | Description |\n|--------|------|-------------|\n"
    for ep in endpoints[:20]:
        endpoint_table += f"| `{ep['method']}` | `{ep['path']}` | {ep['description']} |\n"

    return f"""# {display_name}

> Generated by **ForgeAI** — AI-powered full-stack application generator.

## Features

{features_md}

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | {backend} |
| Database | {db} |
| Frontend | React + Vite |
| Auth | JWT Bearer tokens |

## Quick Start

### Prerequisites

- Python 3.11+
- Node.js 18+ (for frontend)

### Backend

```bash
cd app
pip install -r requirements.txt
uvicorn main:app --reload
```

API available at: `http://localhost:8000`
Interactive docs: `http://localhost:8000/docs`

### Frontend

```bash
npm install
npm run dev
```

Frontend at: `http://localhost:5173`

### Docker

```bash
docker build -t {project_name} .
docker run -p 8000:8000 {project_name}
```

## API Reference

{endpoint_table}

## Database Schema

{len(models)} table(s): {', '.join(m['name'] for m in models)}

See [ARCHITECTURE.md](ARCHITECTURE.md) for full ER diagram.

## Project Structure

```
app/
├── main.py          # FastAPI entry point
├── database.py      # DB connection & session
├── models/          # SQLAlchemy ORM models
├── schemas/         # Pydantic request/response schemas
├── routes/          # API route handlers
├── services/        # Business logic
└── requirements.txt
src/                 # React frontend
```

---
_Built with [ForgeAI](https://github.com/forgeai) · {len(endpoints)} endpoints · {len(models)} models_
"""


def _build_api_docs(endpoints: list[dict]) -> str:
    lines = ["# API Documentation\n", "## Endpoints\n"]
    by_file: dict[str, list] = {}
    for ep in endpoints:
        by_file.setdefault(ep["file"].replace("_routes.py", ""), []).append(ep)

    for resource, eps in sorted(by_file.items()):
        lines.append(f"### {resource.replace('_', ' ').title()}\n")
        for ep in eps:
            lines.append(f"#### `{ep['method']} {ep['path']}`")
            if ep["description"]:
                lines.append(f"\n{ep['description']}\n")
            if ep["response"]:
                lines.append(f"**Response:** `{ep['response']}`\n")
            lines.append("")
    return "\n".join(lines)


def _build_architecture_doc(models: list[dict], project_name: str) -> str:
    er = _build_er_diagram(models)
    return f"""# Architecture — {project_name.replace('_', ' ').title()}

## ER Diagram

{er}

## Backend Architecture

```
FastAPI Application
├── Routing Layer (app/routes/)     → HTTP request handling
├── Service Layer (app/services/)   → Business logic
├── Model Layer (app/models/)       → Database ORM (SQLAlchemy)
├── Schema Layer (app/schemas/)     → Validation (Pydantic v2)
└── Database (app/database.py)      → Session management (SQLite)
```

## Design Patterns

- **Repository pattern**: services own DB queries, routes own HTTP logic
- **Dependency injection**: `get_db` session injected via FastAPI `Depends()`
- **Schema separation**: ORM models never exposed directly; Pydantic schemas serialize responses
- **JWT auth**: Bearer tokens validated via `oauth2_scheme` dependency
"""


def _build_deployment_doc(project_name: str) -> str:
    return f"""# Deployment Guide

## Docker

```bash
# Build
docker build -t {project_name} .

# Run
docker run -d -p 8000:8000 --name {project_name} {project_name}

# Logs
docker logs {project_name}
```

## Railway

1. Push to GitHub
2. Connect repo to [Railway](https://railway.app)
3. Set `PORT=8000` environment variable
4. Railway auto-detects the Dockerfile

## Render

1. Push to GitHub
2. Create new **Web Service** on [Render](https://render.com)
3. Build command: `pip install -r app/requirements.txt`
4. Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`

## Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `SECRET_KEY` | JWT signing secret | Yes |
| `DATABASE_URL` | SQLAlchemy DB URL (default: SQLite) | No |
| `PORT` | Server port (default: 8000) | No |

## Production Checklist

- [ ] Set `SECRET_KEY` to a random 32+ char string
- [ ] Use PostgreSQL instead of SQLite for production
- [ ] Enable HTTPS (handled by Railway/Render automatically)
- [ ] Set `CORS_ORIGINS` to your frontend domain
"""


def generate_repo_docs(
    project_path: str,
    plan: dict,
    architecture: dict,
) -> RepoDocsResult:
    """
    Generate README.md, API_DOCS.md, ARCHITECTURE.md, DEPLOYMENT.md
    inside the project directory.
    """
    result = RepoDocsResult()
    project_name = Path(project_path).name

    try:
        endpoints = _extract_endpoints(project_path)
        models = _extract_models(project_path)

        docs = {
            "README.md": _build_readme(project_name, plan, architecture, endpoints, models),
            "API_DOCS.md": _build_api_docs(endpoints),
            "ARCHITECTURE.md": _build_architecture_doc(models, project_name),
            "DEPLOYMENT.md": _build_deployment_doc(project_name),
        }

        for filename, content in docs.items():
            out_path = Path(project_path) / filename
            out_path.write_text(content, encoding="utf-8")
            result.files_written.append(filename)

        print(f"\n=== REPO DOCS GENERATED ===")
        print(f"  {len(result.files_written)} files: {', '.join(result.files_written)}")
        print(f"  {len(endpoints)} endpoints documented, {len(models)} models in ER diagram")

    except Exception as e:
        result.skipped = True
        result.skip_reason = str(e)
        print(f"Repo docs generation failed: {e}")

    return result
