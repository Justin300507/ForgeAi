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
