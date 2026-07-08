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

# ADR-001 extension (relationship/association metadata, Phase A -- see
# docs/ADR-001-extension-investigation.md). Deliberately mirrors _COLUMN_RE's
# own single-line-call limitation rather than reworking parsing internals:
# this phase's objective is richer deterministic metadata, not parser
# modernization. A relationship() call wrapped across multiple lines won't
# match today, same pre-existing shape of limitation _COLUMN_RE already has
# for Column(...).
_RELATIONSHIP_RE = re.compile(r'^\s+(\w+)\s*=\s*relationship\(([^\n]*)\)\s*$', re.MULTILINE)
_REL_TARGET_RE = re.compile(r'^\s*["\'](\w+)["\']')
_REL_BACK_POPULATES_RE = re.compile(r'back_populates\s*=\s*["\'](\w+)["\']')
_REL_SECONDARY_RE = re.compile(r'secondary\s*=\s*["\'](\w+)["\']')

# ADR-001 extension, Phase C: bare module-level `name = Table("table",
# Base.metadata, Column(...), ...)` association-table constructs --
# structurally different from a `class X(Base):` model (invisible to
# extract_entity_definition entirely), most commonly used for a
# many-to-many association table. This construct is inherently multi-line
# in essentially every real occurrence (it needs multiple Column(...)
# sub-calls), unlike a single Column/relationship line -- so this is
# deliberately scoped to just this one construct rather than a general
# regex-to-AST migration, per the explicit instruction that AST migration
# is an optimization to evaluate later, not this phase's goal.
_TABLE_RE = re.compile(
    r'^(\w+)\s*=\s*Table\(\s*["\'](\w+)["\']\s*,\s*Base\.metadata\s*,\s*(.*?)\n\)',
    re.MULTILINE | re.DOTALL,
)
# Table()-style Column() calls give the column name as the first
# positional string argument (`Column("post_id", Integer, ...)`) rather
# than as the assignment target (`post_id = Column(Integer, ...)`) --
# a different shape than _COLUMN_RE, needing its own pattern. Allows one
# level of nested balanced parens (e.g. ForeignKey("posts.id")) via the
# same idiom already used by deterministic_patcher.py's _DICT_UNPACK_CTOR_RE.
_TABLE_COLUMN_RE = re.compile(r'Column\(\s*["\'](\w+)["\']\s*,\s*((?:[^()]|\([^()]*\))*)\)')


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
class RelationshipDefinition:
    """A single `attr = relationship("Target", ...)` declaration -- captures
    exactly what's stated in the call, nothing derived at parse time.
    `kind` starts unset; it's filled in by the separate cross-entity pass
    `derive_relationship_kinds()` (Phase D), which needs the full entity
    collection (matching back_populates pairs across both sides), not
    just this one file."""
    attr_name: str
    target_class: str
    back_populates: str | None = None
    secondary: str | None = None  # association-table name, if many-to-many
    kind: str | None = None  # "one_to_many" | "many_to_one" | "many_to_many" | "one_to_one" | None (undetermined)


@dataclass
class AssociationTable:
    """A bare module-level `variable_name = Table("table_name",
    Base.metadata, Column(...), ...)` construct. Composite primary key is
    implicit: every column with is_primary_key=True (typically both
    columns, for the common two-FK many-to-many association shape)."""
    variable_name: str
    table_name: str
    source_path: str
    columns: list[FieldDefinition] = field(default_factory=list)

    def composite_primary_key(self) -> list[str]:
        return [c.name for c in self.columns if c.is_primary_key]


@dataclass
class EntityDefinition:
    class_name: str
    table_name: str
    source_path: str
    fields: list[FieldDefinition] = field(default_factory=list)
    relationships: list[RelationshipDefinition] = field(default_factory=list)

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


def _parse_relationship(name: str, args: str) -> RelationshipDefinition:
    target_match = _REL_TARGET_RE.match(args)
    target_class = target_match.group(1) if target_match else "Unknown"
    back_populates_match = _REL_BACK_POPULATES_RE.search(args)
    secondary_match = _REL_SECONDARY_RE.search(args)
    return RelationshipDefinition(
        attr_name=name,
        target_class=target_class,
        back_populates=back_populates_match.group(1) if back_populates_match else None,
        secondary=secondary_match.group(1) if secondary_match else None,
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

    relationships = [
        _parse_relationship(m.group(1), m.group(2))
        for m in _RELATIONSHIP_RE.finditer(body)
    ]

    return EntityDefinition(
        class_name=class_name,
        table_name=table_name,
        source_path=source_path,
        fields=fields,
        relationships=relationships,
    )


def extract_association_table(content: str, source_path: str = "") -> AssociationTable | None:
    """
    Parse the FIRST bare `name = Table("table_name", Base.metadata, ...)`
    construct in a generated file. Returns None if there's no such
    construct (e.g. an ordinary class-based model file) or it has no
    columns (malformed) -- same "don't guess past a shim" discipline as
    extract_entity_definition.
    """
    if not content:
        return None
    m = _TABLE_RE.search(content)
    if not m:
        return None
    variable_name, table_name, body = m.group(1), m.group(2), m.group(3)

    columns = [
        _parse_column(cm.group(1), cm.group(2))
        for cm in _TABLE_COLUMN_RE.finditer(body)
    ]
    if not columns:
        return None

    return AssociationTable(
        variable_name=variable_name,
        table_name=table_name,
        source_path=source_path,
        columns=columns,
    )


def derive_relationship_kinds(entities: list[EntityDefinition]) -> None:
    """
    ADR-001 extension, Phase D (docs/ADR-001-extension-investigation.md):
    accurately derives each RelationshipDefinition.kind by matching
    back_populates pairs across BOTH sides of a relationship, replacing
    Phase B's per-file local heuristic (app/contract/adapter.py's
    enrich_relationships_from_models, which only ever looked at one
    entity's own FK columns in isolation) with a cross-entity-confirmed
    classification.

    Mutates `entities` in place -- sets `.kind` on every RelationshipDefinition
    found. Deliberately leaves `kind=None` (rather than a guessed value)
    whenever the evidence is ambiguous or the two sides of a relationship
    disagree: an honest "undetermined" beats a confident wrong answer,
    the same principle every extractor in this module already follows
    (e.g. `find_model_for_resource` never guesses past a shim).
    """
    by_class = {e.class_name: e for e in entities}

    for entity in entities:
        own_fk_targets = {
            f.fk_target.split(".")[0]
            for f in entity.fields
            if f.is_foreign_key and f.fk_target
        }
        for rel in entity.relationships:
            target_entity = by_class.get(rel.target_class)
            counterpart = None
            if target_entity is not None and rel.back_populates:
                counterpart = next(
                    (r for r in target_entity.relationships
                     if r.attr_name == rel.back_populates and r.target_class == entity.class_name),
                    None,
                )

            if rel.secondary:
                if counterpart is None or counterpart.secondary == rel.secondary:
                    rel.kind = "many_to_many"
                else:
                    rel.kind = None  # counterpart declares a different/no secondary -- inconsistent
                continue

            target_table_guesses = {rel.target_class.lower(), rel.target_class.lower() + "s"}
            this_side_has_fk = bool(own_fk_targets & target_table_guesses)

            if counterpart is None or target_entity is None:
                # No confirmed pair to cross-check against -- fall back to
                # this entity's own FK evidence alone (the same signal
                # Phase B used), still grounded in real Column data, not a
                # blind guess.
                rel.kind = "many_to_one" if this_side_has_fk else "one_to_many"
                continue

            other_target_guesses = {entity.class_name.lower(), entity.class_name.lower() + "s"}
            other_side_has_fk = any(
                f.is_foreign_key and f.fk_target and f.fk_target.split(".")[0] in other_target_guesses
                for f in target_entity.fields
            )

            if this_side_has_fk and not other_side_has_fk:
                rel.kind = "many_to_one"
            elif other_side_has_fk and not this_side_has_fk:
                rel.kind = "one_to_many"
            else:
                # Both sides (or neither) show a matching FK -- can't
                # confidently distinguish this from the one-to-one case
                # without parsing a uselist=False/unique constraint this
                # module doesn't extract -- don't guess.
                rel.kind = None


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
    deliberately phrased as a binding contract, not advisory text.

    Includes relationship guidance (ADR-001 extension, Phase D
    integration) so the schema-generation LLM is told explicitly how to
    expose each relationship type instead of guessing -- this is the
    exact gap ADR-001 originally flagged as out of scope (Experiment 017:
    blog_cms independently inventing a `tag_ids` field for its `tags`
    many-to-many relationship, with nothing telling the schema-gen call
    that field needed to exist or what shape it should take). Callers
    that haven't run `derive_relationship_kinds()` over the full entity
    collection first will see `kind=None` for every relationship here --
    still renders a (less specific) hint rather than silently omitting
    the relationship."""
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

    if entity.relationships:
        lines.append("  Relationships (do not expose these as plain scalar columns):")
        for r in entity.relationships:
            if r.kind == "many_to_many":
                suggested_field = f"{_singular(r.target_class.lower())}_ids"
                lines.append(
                    f"  - {r.attr_name}: many-to-many with {r.target_class} -- "
                    f"expose as a list of {r.target_class} IDs in Create/Update schemas "
                    f"(e.g. '{suggested_field}: list[int]'), not a nested object list"
                )
            elif r.kind == "many_to_one":
                lines.append(
                    f"  - {r.attr_name}: many-to-one to {r.target_class} -- "
                    f"already covered by the foreign-key column above; do not add a "
                    f"separate schema field for this relationship"
                )
            elif r.kind == "one_to_many":
                lines.append(
                    f"  - {r.attr_name}: one-to-many of {r.target_class} -- "
                    f"read-only, only ever appears on the Response schema (e.g. as a "
                    f"nested list), never required on Create/Update"
                )
            else:
                lines.append(
                    f"  - {r.attr_name}: relationship to {r.target_class} "
                    f"(kind undetermined -- treat conservatively, prefer read-only exposure)"
                )
    return "\n".join(lines)
