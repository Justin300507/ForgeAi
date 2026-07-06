"""
Deterministic Reference-Data Seeder (ADR-002 candidate).

Replaces the zero-insert minimal seed_routes.py fallback stub in
v6_orchestrator.py with a fully deterministic generator that reuses
entity_metadata.py's already-parsed model data to seed lookup/reference
tables. Zero LLM calls, zero prompt generation, zero AI reasoning
anywhere in this module -- see
docs/superpowers/specs/2026-07-06-deterministic-seed-generator-design.md
for the frozen design this implements.
"""
from __future__ import annotations

import os

from app.services.entity_metadata import EntityDefinition, extract_entity_definition

TARGET_ROWS = 3
GENERATOR_VERSION = 1


def discover_models(project_path: str) -> dict:
    """Parse every app/models/*.py file into an EntityDefinition, keyed by
    table_name. Files that parse to None (re-export shims, non-model
    files) are skipped. The users/user table is always excluded."""
    models_dir = os.path.join(project_path, "app", "models")
    entities: dict = {}
    if not os.path.isdir(models_dir):
        return entities
    for fname in os.listdir(models_dir):
        if not fname.endswith(".py") or fname == "__init__.py":
            continue
        fpath = os.path.join(models_dir, fname)
        try:
            with open(fpath, "r", encoding="utf-8") as fh:
                content = fh.read()
        except OSError:
            continue
        entity = extract_entity_definition(content, source_path=fpath)
        if entity is None:
            continue
        if entity.table_name in ("users", "user"):
            continue
        entities[entity.table_name] = entity
    return entities


def find_lookup_entities(entities: dict):
    """Returns (eligible_entities, exclusion_log_lines).

    Stage 1 (candidates): any entity that is the FK target of some
    column -- including its own self-referential column.
    Stage 2 (eligibility, fixed point): a candidate is eligible only if
    every required (nullable=False, no default) FK it declares points to
    another currently-eligible entity. A required self-referential FK
    can never be satisfied by the first inserted row and is excluded
    unconditionally, before the fixed-point loop even starts. Iterates
    until a pass removes nothing (always terminates -- the set only
    shrinks).
    """
    all_entities = [e for e in entities.values() if e.table_name not in ("users", "user")]

    target_tables = set()
    for e in all_entities:
        for f in e.fields:
            if f.is_foreign_key and f.fk_target:
                target_tables.add(f.fk_target.split(".")[0])

    candidates = {
        e.table_name: e for e in all_entities if e.table_name in target_tables
    }

    exclusion_log = []

    for table_name, entity in list(candidates.items()):
        for f in entity.fields:
            if (f.is_foreign_key and f.fk_target and not f.nullable
                    and not f.has_default
                    and f.fk_target.split(".")[0] == table_name):
                exclusion_log.append(
                    f"excluded {table_name}: required self-referential FK "
                    "(no prior row exists to satisfy it)"
                )
                del candidates[table_name]
                break

    eligible = dict(candidates)
    changed = True
    while changed:
        changed = False
        for table_name, entity in list(eligible.items()):
            for f in entity.fields:
                if not f.is_foreign_key or not f.fk_target:
                    continue
                if f.nullable or f.has_default:
                    continue
                fk_table = f.fk_target.split(".")[0]
                if fk_table not in eligible:
                    exclusion_log.append(
                        f"excluded {table_name}: required FK -> {fk_table} "
                        "outside the deterministic lookup graph"
                    )
                    del eligible[table_name]
                    changed = True
                    break
            if changed:
                break

    return list(eligible.values()), exclusion_log


def topological_order(entities: list):
    """Kahn's algorithm over the FK subgraph restricted to `entities`
    (self-referential edges are ignored -- entities with a REQUIRED
    self-reference were already excluded by find_lookup_entities; a
    nullable self-reference needs no ordering, since it's simply omitted
    at insert time). Returns None if a cycle prevents full ordering --
    the caller must treat this as "abort, fall back to the static stub."
    """
    names = {e.table_name for e in entities}
    by_name = {e.table_name: e for e in entities}

    in_degree = {name: 0 for name in names}
    dependents = {name: [] for name in names}

    for e in entities:
        deps = set()
        for f in e.fields:
            if f.is_foreign_key and f.fk_target:
                target = f.fk_target.split(".")[0]
                if target in names and target != e.table_name:
                    deps.add(target)
        in_degree[e.table_name] = len(deps)
        for dep in deps:
            dependents[dep].append(e.table_name)

    queue = sorted(name for name, deg in in_degree.items() if deg == 0)
    ordered = []
    while queue:
        queue.sort()
        current = queue.pop(0)
        ordered.append(current)
        for dependent in dependents[current]:
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                queue.append(dependent)

    if len(ordered) != len(entities):
        return None

    return [by_name[name] for name in ordered]


from datetime import datetime, timezone


_BOOL_TYPES = {"Boolean"}
_INT_TYPES = {"Integer", "SmallInteger", "BigInteger", "Numeric", "Float", "DECIMAL"}
_DATE_TYPES = {"Date", "DateTime", "Time", "TIMESTAMP"}
_UUID_TYPES = {"UUID"}


def generate_field_value(field, class_name: str, loop_var: str = "i") -> str:
    """Returns a Python source EXPRESSION (referencing `loop_var`, the
    runtime loop counter) to embed directly in generated code -- never a
    concrete literal, since the actual row index isn't known until
    runtime (count-based idempotency starts the loop at `existing`, not
    always 0). Purely type-driven -- zero semantic guessing of field
    meaning, zero keyword matching on field/entity names."""
    t = field.sqlalchemy_type
    if t in _BOOL_TYPES:
        return f"({loop_var} % 2 == 0)"
    if t in _INT_TYPES:
        return f"({loop_var} + 1)"
    if t in _DATE_TYPES:
        return f"datetime.utcnow() - timedelta(days={loop_var})"
    if t in _UUID_TYPES:
        return "str(uuid.uuid4())"
    # String/Text/Unicode/Enum/anything unrecognized: safe non-null
    # string fallback -- never leave a NOT NULL column unset.
    return f'"{class_name} " + str({loop_var} + 1)'


def _module_name(entity) -> str:
    return os.path.splitext(os.path.basename(entity.source_path))[0]


def render_seed_routes(entities_in_order: list) -> str:
    """Emits the complete seed_routes.py source: version header, imports,
    one count-gated _seed_<table>(db) helper per lookup entity (in
    dependency order), and the POST /seed handler."""
    timestamp = datetime.now(timezone.utc).isoformat()
    class_name_by_table = {e.table_name: e.class_name for e in entities_in_order}

    lines = [
        "# Generated by ForgeAI Deterministic Seeder",
        "# ADR-002",
        f"# Generator Version: {GENERATOR_VERSION}",
        f"# Timestamp: {timestamp}",
        "from fastapi import APIRouter, Depends",
        "from sqlalchemy.orm import Session",
        "from sqlalchemy.exc import IntegrityError",
        "from datetime import datetime, timedelta",
        "import uuid",
        "from app.database import get_db",
    ]
    for e in entities_in_order:
        lines.append(f"from app.models.{_module_name(e)} import {e.class_name}")
    lines.append("")
    lines.append("seed_router = APIRouter()")
    lines.append("")

    for e in entities_in_order:
        lines.extend(_render_entity_seed_fn(e, class_name_by_table))
        lines.append("")

    lines.append("@seed_router.post('/seed')")
    lines.append("def seed_data(db: Session = Depends(get_db)):")
    lines.append("    summary = {}")
    for e in entities_in_order:
        lines.append(f"    summary['{e.table_name}'] = _seed_{e.table_name}(db)")
    lines.append("    return {'seeded': True, 'summary': summary}")
    lines.append("")

    return "\n".join(lines)


def _render_entity_seed_fn(entity, class_name_by_table: dict) -> list:
    fn_name = f"_seed_{entity.table_name}"
    class_name = entity.class_name

    internal_fk_fields = [
        f for f in entity.fields
        if f.is_foreign_key and f.fk_target
        and f.fk_target.split(".")[0] in class_name_by_table
        and f.fk_target.split(".")[0] != entity.table_name
    ]
    internal_fk_names = {f.name for f in internal_fk_fields}

    body = [f"def {fn_name}(db: Session) -> dict:"]
    body.append(f"    existing = db.query({class_name}).count()")
    body.append(f"    if existing >= {TARGET_ROWS}:")
    body.append(
        f"        return {{'inserted': 0, 'skipped': {TARGET_ROWS}, "
        f"'already_existed': existing}}"
    )

    pool_vars = {}
    for f in internal_fk_fields:
        parent_class = class_name_by_table[f.fk_target.split(".")[0]]
        parent_var = f"_{f.name}_pool"
        pool_vars[f.name] = parent_var
        body.append(f"    {parent_var} = db.query({parent_class}).all()")

    if pool_vars:
        cond = " or ".join(f"not {v}" for v in pool_vars.values())
        body.append(f"    if {cond}:")
        body.append(
            f"        return {{'inserted': 0, 'skipped': {TARGET_ROWS}, "
            f"'already_existed': existing}}"
        )

    body.append("    inserted = 0")
    body.append(f"    for i in range(existing, {TARGET_ROWS}):")
    body.append("        try:")

    kwargs = []
    for f in entity.fields:
        if f.is_primary_key:
            continue
        if f.is_foreign_key:
            if f.name in internal_fk_names:
                parent_var = pool_vars[f.name]
                kwargs.append(f"{f.name}={parent_var}[i % len({parent_var})].id")
            # required-external FKs can't reach here (excluded upstream by
            # find_lookup_entities); nullable-external FKs are simply
            # omitted -- no runtime query, no conditional branch.
            continue
        kwargs.append(f"{f.name}={generate_field_value(f, class_name=class_name)}")

    kwargs_str = ", ".join(kwargs)
    body.append(f"            db.add({class_name}({kwargs_str}))")
    body.append("            db.commit()")
    body.append("            inserted += 1")
    body.append("        except IntegrityError:")
    body.append("            db.rollback()")
    body.append(
        f"    return {{'inserted': inserted, "
        f"'skipped': {TARGET_ROWS} - existing - inserted, "
        f"'already_existed': existing}}"
    )
    return body


import time


def generate(project_path: str):
    """Top-level entry point for v6_orchestrator.py. Returns
    (source_code, telemetry) on success, or (None, telemetry) if
    deterministic generation isn't applicable or fails for any reason --
    the caller MUST fall back to the static stub in that case. This
    function never raises; every failure path is caught here, which is
    the single fallback boundary for the whole feature."""
    start = time.time()
    telemetry = {
        "adr002_enabled": False,
        "entities_discovered": 0,
        "lookup_entities": 0,
        "fallback_used": True,
        "fallback_reason": "",
        "generation_time_ms": 0.0,
        "exclusions": [],
    }
    try:
        entities = discover_models(project_path)
        telemetry["entities_discovered"] = len(entities)
        if not entities:
            telemetry["fallback_reason"] = "no models discovered"
            telemetry["generation_time_ms"] = round((time.time() - start) * 1000, 2)
            return None, telemetry

        eligible, exclusion_log = find_lookup_entities(entities)
        telemetry["exclusions"] = exclusion_log
        telemetry["lookup_entities"] = len(eligible)
        if not eligible:
            telemetry["fallback_reason"] = "no lookup entities"
            telemetry["generation_time_ms"] = round((time.time() - start) * 1000, 2)
            return None, telemetry

        ordered = topological_order(eligible)
        if ordered is None:
            telemetry["fallback_reason"] = "FK cycle detected"
            telemetry["generation_time_ms"] = round((time.time() - start) * 1000, 2)
            return None, telemetry

        source = render_seed_routes(ordered)
        telemetry["adr002_enabled"] = True
        telemetry["fallback_used"] = False
        telemetry["generation_time_ms"] = round((time.time() - start) * 1000, 2)
        return source, telemetry
    except Exception as exc:
        telemetry["fallback_reason"] = f"exception: {exc}"
        telemetry["generation_time_ms"] = round((time.time() - start) * 1000, 2)
        return None, telemetry
