# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Is

ForgeAI is an AI-powered code generator. Given a plain-English project idea, it runs a multi-stage LLM pipeline that produces a complete, validated FastAPI + React project. The generated project is written to `generated_projects/`, validated by a suite of static analysis checks, tested at runtime, then exported as a zip.

## Running the Server

```bash
cd backend
# Activate venv first (Windows) — PYTHONIOENCODING required for Cerebras Unicode output
$env:PYTHONIOENCODING = "utf-8"
.\venv\Scripts\activate
uvicorn main:app --reload
```

The server runs on `http://localhost:8000`. Endpoints:
- `POST /project` (V5 full pipeline)
- `POST /project/v6` (V6 multi-agent team)
- `POST /project/v7` (V7 self-improving — recommended)
- `POST /improve` (trigger V7 improvement cycle manually)
- `GET /leaderboard`, `GET /research/latest`, `GET /benchmark/comparison`

## Running V7 Tests

```bash
cd backend
$env:PYTHONIOENCODING = "utf-8"
# Checks 2-5 (fast, no full generation):
.\venv\Scripts\python.exe test_v7.py --all
# Check 1 (full E2E):
.\venv\Scripts\python.exe test_v7.py --e2e --idea "A todo app with user login"
# Holy grail (baseline → improve → retest, needs 6+ project generations):
.\venv\Scripts\python.exe test_v7.py --holy-grail --n-projects 3 --benchmark 3
```

## Required Environment Variables

Create `backend/.env`:
```
CEREBRAS_API_KEY=...
GROQ_API_KEY=...
OPENROUTER_API_KEY=...
GEMINI_API_KEY=...
```

## Running Tests

Fixture-based regression tests (skip planner/architect LLM calls, use cached fixtures):
```bash
cd backend
python tests/run_fixture_regression.py          # all fixtures
python test_fixture.py                          # single fixture (library_system)
python test_endpoint_coverage.py                # endpoint coverage test
```

Fixtures live in `test_fixtures/*.json`. Baselines are stored in `tests/snapshots/`. On first run with no snapshot, the current result becomes the baseline. On subsequent runs, regressions (new errors or lower score) require manual confirmation before updating the baseline.

## Generation Pipeline

`POST /project` triggers this sequence in `project_service.py`:

1. **Planner** — LLM produces a `ProjectPlan` (project name, features, tech stack, DB entities, API modules)
2. **Architect** — LLM produces an `ArchitecturePlan` (API endpoints with `method`/`path`/`file`, DB schema, folder structure)
3. **Backend Generator** — LLM generates all Python files (routes, models, schemas, services, main.py)
4. **Frontend Generator** — LLM generates React/JSX files
5. **File Writer** — writes everything to `generated_projects/<project_name>/`
6. **Validation Loop** (up to 2 fix attempts):
   - Runs `validate_project()` — see Validators below
   - Groups errors by file, calls `generate_fix()` or `generate_missing_file()` per file
   - Autocorrects endpoint paths and router names before writing fixes
7. **Architecture Repair** — if architecture-level errors remain, calls `generate_architecture_fix()` to rewrite affected route files wholesale
8. **Runtime Validation** (only if static validation passed):
   - `BackendRunner` installs the project's `app/requirements.txt`, starts uvicorn, hits `/health`, then runs endpoint smoke tests
   - Up to 2 runtime fix attempts via `generate_runtime_fix()`
9. **Export** — creates a zip only if both static validation and runtime passed
10. **Forge Score** — 0–100 score (A/F grade) penalizing each error category by weight

## LLM Provider System

`app/providers/ai_provider.py` dispatches to: **Cerebras → Groq → OpenRouter → Gemini → Ollama** (auto mode tries each in order, falls back on exception). Each provider is a standalone module in `app/providers/`. Cerebras uses the OpenAI SDK pointed at `https://api.cerebras.ai/v1` with model `gpt-oss-120b`.

LLM responses for Planner and Architect stages are cached in `backend/llm_cache/` (SHA256-keyed JSON files). Backend and Frontend generation is not cached because the architecture input changes per run.

## Validators (validate_project)

`validator_service.py` orchestrates these checks against the generated project directory:

| Validator | What it checks |
|---|---|
| `architecture_validator` | All files/endpoints in the architecture plan actually exist |
| `endpoint_validator` | Every endpoint in metadata is implemented; no orphan route files |
| `router_export_validator` | Each route file exports a `{resource}_router` symbol |
| `undefined_symbol_validator` | No references to symbols that don't exist in scope |
| `import_validator` / `validate_imported_symbols` | All `from app.X import Y` targets exist and export Y |
| `orm_validator` | No Flask-SQLAlchemy style (`db.Model`, `db = SQLAlchemy()`) |
| `database_validator` | DB dependency function is always named `get_db` |
| `schema_model_validator` | Pydantic schemas and SQLAlchemy models don't conflict |
| `session_validator` | No session leaks (sessions opened but not closed) |
| `self_shadow_validator` | No functions that call themselves under a shadowed name |
| `stub_handler_validator` | No route handlers with just `pass` or placeholder bodies |
| `global_statement_validator` | No module-level `global` statements |
| `duplicate_class_validator` | No duplicate class definitions in the same file |
| `validate_route_quality` | Every route file has `APIRouter(` and at least one endpoint |
| `py_compile` | Every `.py` file compiles without syntax errors |

## Project Contract for Generated Code

The rules in `app/prompts/shared_contract.py` are injected into every LLM prompt. Key constraints enforced both by prompts and validators:

- **Framework**: FastAPI only. Never Flask or Django.
- **Router naming**: `{resource}_router = APIRouter()` (e.g. `user_router`, `task_router`), never just `router`.
- **SQLAlchemy models** go in `app/models/`, inherit from `Base`. **Pydantic schemas** go in `app/schemas/`, inherit from `BaseModel`. Never mix them.
- **DB dependency**: always named `get_db`, never `db`.
- All imports are absolute starting with `app.` and reference specific submodules (e.g. `from app.models.user import User`, not `from app.models import User`).
- Route handler parameters: body params first, then `Path()`/`Query()`/`Depends()` params.
- No `async def` with synchronous SQLAlchemy Session calls (`db.query`, `db.commit`).
- Nullable DB columns → `Optional[Type] = None` in schemas.

## Key File Locations

- `backend/main.py` — FastAPI app entry point (thin, just imports and wires routes)
- `backend/app/services/project_service.py` — full end-to-end generation pipeline
- `backend/app/services/validator_service.py` — orchestrates all static validators
- `backend/app/prompts/shared_contract.py` — the contract injected into all prompts
- `backend/app/providers/ai_provider.py` — provider dispatch and auto-fallback
- `backend/app/utils/llm_cache.py` — file-based LLM response cache
- `backend/app/runtime/backend_runner.py` — spawns uvicorn, runs smoke tests
- `test_fixtures/` — JSON fixtures with `{plan, architecture}` for regression tests
- `generated_projects/` — output directory (git-ignored)
- `backend/llm_cache/` — cached LLM responses (git-ignored)
