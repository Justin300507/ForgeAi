"""
ContractAdapter — derives a best-effort AppContract from the CURRENT,
pre-existing architect output (ArchitecturePlan), per the migration path
in docs/FORGEAI_VNEXT_REPORT.md S5.2: "introduce the contract as derived
from the current architect output first (adapter), then tighten the
architect prompt to emit it natively. No big-bang."

ArchitecturePlan (app/models/architecture_models.py) predates the contract
and simply doesn't carry several fields AppContract has a slot for --
relationships, response/request schema definitions, frontend api_calls,
file exports, and dependency lists are all generated LATER in the pipeline
(backend/frontend generation, requirements.txt/package.json writing), not
at the architecture stage. Those fields are left empty here rather than
guessed at. That's intentional and matches AppContract's design: every
field has a safe default specifically so a partial projection like this
one is valid (see app/contract/models.py's docstring and the
"minimal = AppContract(app=ContractApp(name='x'))" case its test covers).

Nothing in the live pipeline calls this yet -- it exists so the next PR
(a warn-only ContractConformanceValidator) has something to run against
without requiring the architect prompt to change first.
"""
from __future__ import annotations

import re

from app.contract.models import (
    AppContract, ContractApp, ContractEndpoint, ContractEntity, ContractField,
    ContractFile, ContractFrontend, ContractRoute,
)
from app.models.architecture_models import ArchitecturePlan


_INVARIANT_WORDS = {"status", "series", "species", "news"}


def _singularize(word: str) -> str:
    """Crude English singularizer -- good enough for table_name -> entity
    class name (tasks -> Task, categories -> Category). Not linguistically
    complete; wrong on irregular plurals, which is acceptable for a
    best-effort adapter (the entity's real name is confirmed once the
    architect emits contracts natively). Verified against real architect
    output: naively stripping a trailing "s" turned "status" into "statu",
    so words ending "us"/"ss" (and a short invariant-word list) are
    excluded from stripping."""
    lower = word.lower()
    if lower in _INVARIANT_WORDS:
        return word
    if word.endswith("ies") and len(word) > 3:
        return word[:-3] + "y"
    if word.endswith("ses") or word.endswith("xes") or word.endswith("ches"):
        return word[:-2]
    if word.endswith("s") and not word.endswith(("ss", "us")):
        return word[:-1]
    return word


def _table_to_entity_name(table_name: str) -> str:
    parts = re.split(r"[_\-]+", table_name.strip())
    return "".join(p.capitalize() for p in (_singularize(parts[-1]),) if parts) or table_name.capitalize()


def _endpoint_name(method: str, path: str) -> str:
    """Best-effort operation name from method + path. Distinguishes a
    collection endpoint (GET /tasks -> list_tasks) from a detail endpoint
    (GET /tasks/{id} -> get_task) by whether the path's last segment is a
    path parameter."""
    segments = [s for s in path.split("/") if s]
    is_detail = bool(segments) and segments[-1].startswith("{")
    noun_segments = [s for s in segments if not s.startswith("{")]
    noun = _singularize(noun_segments[-1]) if noun_segments else "resource"
    method_u = method.upper()
    if method_u == "GET":
        return f"get_{noun}" if is_detail else f"list_{noun}s"
    if method_u == "POST":
        return f"create_{noun}"
    if method_u in ("PUT", "PATCH"):
        return f"update_{noun}"
    if method_u == "DELETE":
        return f"delete_{noun}"
    return f"{method_u.lower()}_{noun}"


def _page_to_route_path(page: str) -> str:
    """Best-effort URL path from a frontend page name, e.g.
    'TaskListPage.jsx' -> '/task-list', 'DashboardPage' -> '/dashboard'.
    Verified against real architect output where page names include a
    file extension (LoginPage.jsx) -- stripping "Page" as a raw substring
    before removing the extension left a stray '-.jsx' in the path."""
    name = re.sub(r"\.(jsx?|tsx?)$", "", page.strip(), flags=re.IGNORECASE)
    name = re.sub(r"Page$", "", name)
    slug = re.sub(r"(?<!^)(?=[A-Z])", "-", name).lower().strip("-")
    return "/" + slug if slug else "/"


def _infer_file_kind(path: str) -> str:
    p = path.lower()
    if "model" in p:
        return "model"
    if "schema" in p:
        return "schema"
    if "route" in p:
        return "route"
    if "service" in p:
        return "service"
    if "util" in p:
        return "util"
    if "config" in p or path.lower().endswith(".env"):
        return "config"
    return "util"


def _infer_entity_for_path(path: str, entity_names: list[str]) -> str | None:
    """Match a route/endpoint path segment against known entity names, e.g.
    '/tasks' or 'app/routes/task_routes.py' against entity 'Task'. Matches
    whole path/filename segments, not raw substrings -- verified against
    real architect output where naive substring containment matched
    'POST /auth/login' against entity 'Log' (table activity_logs) purely
    because "log" is a substring of "login"."""
    words = set(re.split(r"[/_\-.{}]+", path.lower())) - {""}
    for name in entity_names:
        singular = name.lower()
        plural_guess = singular + ("es" if singular.endswith(("s", "x", "ch", "sh")) else "s")
        if singular in words or plural_guess in words:
            return name
    return None


def from_architecture_plan(
    idea:         str,
    project_name: str,
    architecture: ArchitecturePlan,
    category:     str = "",
) -> AppContract:
    """
    Project the current ArchitecturePlan into an AppContract. Best-effort:
    entities/fields/endpoints/routes/files are derived where the source
    data supports it; relationships, schemas, frontend api_calls, and deps
    are left empty (the architecture stage doesn't produce them yet).
    """
    entities: list[ContractEntity] = []
    for table in architecture.database_schema:
        entities.append(ContractEntity(
            name=_table_to_entity_name(table.table_name),
            table_name=table.table_name,
            fields=[
                ContractField(name=c.name, type=c.type, nullable=c.is_nullable)
                for c in table.columns
            ],
        ))
    entity_names = [e.name for e in entities]

    endpoints: list[ContractEndpoint] = []
    for ep in architecture.api_endpoints:
        endpoints.append(ContractEndpoint(
            method=ep.method,
            path=ep.path,
            name=_endpoint_name(ep.method, ep.path),
            entity=_infer_entity_for_path(ep.path, entity_names) or _infer_entity_for_path(ep.file, entity_names),
        ))

    routes = [
        ContractRoute(path=_page_to_route_path(page), page=page)
        for page in architecture.frontend_structure.pages
    ]

    files = [
        ContractFile(path=p, kind=_infer_file_kind(p))
        for p in architecture.folder_structure.backend
    ]

    return AppContract(
        app=ContractApp(name=project_name, category=category, description=idea),
        entities=entities,
        endpoints=endpoints,
        frontend=ContractFrontend(
            routes=routes,
            components=list(architecture.frontend_structure.components),
        ),
        files=files,
    )
