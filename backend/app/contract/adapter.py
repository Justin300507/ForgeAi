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
    ContractFile, ContractFrontend, ContractRelationship, ContractRoute,
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


def enrich_relationships_from_models(contract: AppContract, project_path) -> int:
    """
    ADR-001 extension, Phase B (docs/ADR-001-extension-investigation.md):
    populates each ContractEntity.relationships from the REAL generated
    models on disk, activating check_contract_conformance's
    `_check_relationship_targets_exist()` -- previously permanently inert,
    since `from_architecture_plan()` only ever derives entities from the
    architect's PLAN, which (per this module's own docstring) doesn't
    carry relationship data at all.

    Reuses entity_metadata.py's Phase A extraction (`extract_entity_definition`,
    now populating `.relationships`) rather than re-parsing model files --
    "entity_metadata.py is the only parser" holds for this extension too.

    Matches a parsed model to its ContractEntity by `table_name` (both
    ultimately reference the same real database table, more reliable than
    matching on two independently-derived name strings). `rel.target_class`
    is used directly as `ContractRelationship.target` -- this is the actual
    SQLAlchemy class name, which `_table_to_entity_name()`'s singularization
    (used to name every ContractEntity) matches for ordinary singular/plural
    conventions but is not guaranteed to for irregular ones; a known,
    accepted limitation of this best-effort adapter, same as
    `_table_to_entity_name`'s own docstring already states for entity
    naming in general.

    `kind` is a per-file, LOCAL best-effort guess, not yet the fully
    accurate cross-entity derivation (that needs a separate pass matching
    back_populates pairs across both sides of a relationship -- Phase D,
    deliberately not done here): `secondary` present -> "many_to_many";
    else this entity itself holds a foreign key to the target's table ->
    "many_to_one"; else assumed to be the reverse (target holds the FK) ->
    "one_to_many". `_check_relationship_targets_exist()` doesn't read
    `kind` at all, so this guess only matters if something else consumes
    it later -- flagged here rather than silently relied upon.

    Returns the number of relationships added (0 if app/models/ doesn't
    exist or no entity matched).
    """
    import os as _os

    from app.services.entity_metadata import extract_entity_definition

    models_dir = _os.path.join(str(project_path), "app", "models")
    if not _os.path.isdir(models_dir):
        return 0

    added = 0
    for fname in _os.listdir(models_dir):
        if not fname.endswith(".py") or fname == "__init__.py":
            continue
        fpath = _os.path.join(models_dir, fname)
        try:
            with open(fpath, "r", encoding="utf-8") as fh:
                content = fh.read()
        except OSError:
            continue

        entity_def = extract_entity_definition(content, source_path=fpath)
        if entity_def is None or not entity_def.relationships:
            continue

        contract_entity = next(
            (e for e in contract.entities if e.table_name == entity_def.table_name),
            None,
        )
        if contract_entity is None:
            continue  # not in the architect's plan -- nothing to enrich

        own_fk_targets = {
            f.fk_target.split(".")[0]
            for f in entity_def.fields
            if f.is_foreign_key and f.fk_target
        }
        for rel in entity_def.relationships:
            target_table_guesses = {rel.target_class.lower(), rel.target_class.lower() + "s"}
            if rel.secondary:
                kind = "many_to_many"
            elif own_fk_targets & target_table_guesses:
                kind = "many_to_one"
            else:
                kind = "one_to_many"
            contract_entity.relationships.append(ContractRelationship(
                kind=kind,
                target=rel.target_class,
                back_populates=rel.back_populates,
            ))
            added += 1

    return added
