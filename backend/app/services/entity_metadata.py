"""
Entity Metadata Extractor — a reusable, deterministic layer that parses an
already-generated SQLAlchemy model file into a structured EntityDefinition
(fields, types, nullability, defaults, primary/foreign keys).

Why this exists (Experiment 017, 2026-07-06): the #1 recurring architectural
failure this cycle was model/schema field-name drift -- e.g. a SQLAlchemy
model declaring a single `name` column while the corresponding Pydantic
Create schema only offers `first_name`/`last_name`. Root-caused to two
compounding bugs in `parallel_backend_service.py`'s Wave 3 (schema
generation): (1) the model-content lookup used a naive
`resource.py` / `resource[:-1]+".py"` filename guess that doesn't handle
all singular/plural conventions, and (2) worse, Wave 2.5's singular-shim
step (`created shim app/models/{singular}.py`) registers a bare two-line
re-export (`from app.models.X import Y`) into the SAME `model_contents`
lookup dict the real model lives in -- so Wave 3 can silently receive a
contentless shim instead of the actual Column definitions, leaving the
schema-generation LLM call with zero real field information and no choice
but to guess.

This module doesn't change *how* the model gets generated -- it only makes
the *already-generated* model consumable as structured ground truth by
whatever needs it next (today: schema generation; reusable later for
routes, validators, docs, or a future full AppContract).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


_CLASS_RE = re.compile(r'^class\s+(\w+)\s*\(\s*Base\s*\)\s*:', re.MULTILINE)
_TABLENAME_RE = re.compile(r'__tablename__\s*=\s*["\'](\w+)["\']')
_COLUMN_RE = re.compile(r'^\s+(\w+)\s*=\s*Column\(([^\n]*)\)\s*$', re.MULTILINE)
_FK_RE = re.compile(r'ForeignKey\(\s*["\'](\w+)\.(\w+)["\']')


@dataclass
class FieldDefinition:
    name: str
    sqlalchemy_type: str
    nullable: bool
    has_default: bool
    is_primary_key: bool
    is_foreign_key: bool
    fk_target: str | None = None  # e.g. "users.id"


@dataclass
class EntityDefinition:
    class_name: str
    table_name: str
    source_path: str
    fields: list[FieldDefinition] = field(default_factory=list)

    def field_names(self) -> set[str]:
        return {f.name for f in self.fields}

    def required_fields(self) -> list[FieldDefinition]:
        """Fields a client MUST supply for INSERT to succeed: not nullable,
        no default, not a primary key, not a foreign key (those are always
        supplied programmatically by the route, e.g. user_id=current_user.id)."""
        return [
            f for f in self.fields
            if not f.nullable and not f.has_default
            and not f.is_primary_key and not f.is_foreign_key
        ]


def _parse_column(name: str, args: str) -> FieldDefinition:
    type_match = re.match(r'\s*(\w+)', args)
    sqlalchemy_type = type_match.group(1) if type_match else "Unknown"
    is_pk = "primary_key" in args
    fk_match = _FK_RE.search(args)
    is_fk = fk_match is not None
    fk_target = f"{fk_match.group(1)}.{fk_match.group(2)}" if fk_match else None
    nullable = "nullable=False" not in args
    has_default = "default=" in args or "server_default=" in args
    return FieldDefinition(
        name=name,
        sqlalchemy_type=sqlalchemy_type,
        nullable=nullable,
        has_default=has_default,
        is_primary_key=is_pk,
        is_foreign_key=is_fk,
        fk_target=fk_target,
    )


def extract_entity_definition(model_content: str, source_path: str = "") -> EntityDefinition | None:
    """
    Parse the FIRST `class X(Base): ...` block in a generated SQLAlchemy
    model file into a structured EntityDefinition. Returns None if the
    content has no real model class (e.g. a bare re-export shim like
    `from app.models.contacts import Contact` -- exactly the case that
    caused Experiment 017's root cause: those must NOT be mistaken for a
    real model with column data).
    """
    if not model_content:
        return None
    class_match = _CLASS_RE.search(model_content)
    if not class_match:
        return None

    class_name = class_match.group(1)
    body_start = class_match.end()
    next_class = _CLASS_RE.search(model_content, body_start)
    body = model_content[body_start:next_class.start() if next_class else len(model_content)]

    tn_match = _TABLENAME_RE.search(body)
    table_name = tn_match.group(1) if tn_match else class_name.lower()

    fields = [
        _parse_column(m.group(1), m.group(2))
        for m in _COLUMN_RE.finditer(body)
    ]
    if not fields:
        return None  # a shim or an otherwise columnless class -- not a real model

    return EntityDefinition(
        class_name=class_name,
        table_name=table_name,
        source_path=source_path,
        fields=fields,
    )


def _singular(name: str) -> str:
    if name.endswith("ies"):
        return name[:-3] + "y"
    if name.endswith("ss") or name.endswith("us"):
        return name
    if name.endswith("s") and len(name) > 2:
        return name[:-1]
    return name


def _plural(name: str) -> str:
    if name.endswith("y") and len(name) > 1 and name[-2].lower() not in "aeiou":
        return name[:-1] + "ies"
    if name.endswith(("s", "x", "ch", "sh")):
        return name + "es"
    return name + "s"


def find_model_for_resource(
    model_contents: dict[str, str], resource: str,
) -> EntityDefinition | None:
    """
    Find the real generated model matching a Wave-3 `resource` name
    (which may be singular or plural depending on how the architect's
    endpoint spec was written -- see `_group_endpoints_by_resource`).

    Matches by parsed `table_name`/`class_name`, not by filename guessing,
    so a bare re-export shim (which parses to None -- no Column data) can
    never be mistaken for the real model, and singular/plural naming
    mismatches (contact vs contacts) resolve correctly regardless of which
    file happens to hold the real Column definitions.
    """
    candidates = {resource, _singular(resource), _plural(resource)}
    best: EntityDefinition | None = None
    for path, content in model_contents.items():
        entity = extract_entity_definition(content, source_path=path)
        if entity is None:
            continue
        names = {entity.table_name, entity.class_name.lower(),
                  _singular(entity.table_name), _plural(entity.table_name)}
        if names & candidates:
            # Prefer whichever match has the most fields (the fullest,
            # most authoritative definition) if more than one resolves.
            if best is None or len(entity.fields) > len(best.fields):
                best = entity
    return best


def render_field_manifest(entity: EntityDefinition) -> str:
    """Render an explicit, unambiguous field list for prompt injection --
    deliberately phrased as a binding contract, not advisory text."""
    lines = [f"Table: {entity.table_name}  (SQLAlchemy class: {entity.class_name})"]
    for f in entity.fields:
        tags = []
        if f.is_primary_key:
            tags.append("primary key, do not expose in Create schema")
        if f.is_foreign_key:
            tags.append(f"foreign key -> {f.fk_target}, supplied by the route, not the client")
        if not f.nullable and not f.has_default and not f.is_primary_key and not f.is_foreign_key:
            tags.append("REQUIRED -- must appear in the Create schema under this exact name")
        elif f.nullable or f.has_default:
            tags.append("optional")
        tag_str = f"  ({'; '.join(tags)})" if tags else ""
        lines.append(f"  - {f.name}: {f.sqlalchemy_type}{tag_str}")
    return "\n".join(lines)
