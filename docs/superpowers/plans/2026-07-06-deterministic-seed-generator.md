# Deterministic Reference-Data Seeder (ADR-002 candidate) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the zero-insert minimal `seed_routes.py` fallback stub with a fully
deterministic generator that reuses `entity_metadata.py` to seed lookup/reference tables,
so FK-validated CRUD Create calls stop failing whenever the LLM omits a real seed script.

**Architecture:** New module `backend/app/services/deterministic_seed_generator.py`
(discovery → FK-target candidacy → transitive eligibility → topological order → typed
value generation → source rendering), wired into a single call site in
`backend/app/services/v6_orchestrator.py`. Falls back to today's exact static stub on any
failure. Zero LLM calls anywhere in this path.

**Tech Stack:** Python 3, stdlib only (`re` via the existing `entity_metadata.py`, `os`,
`time`, `uuid`, `datetime`). No new dependencies. Tests are standalone assert-based scripts
(this repo has no pytest installed and no existing test-suite convention — see Global
Constraints) run directly with the venv interpreter, matching this project's established
"$0, local, no LLM/server" verification style.

## Global Constraints

- **Spec is frozen**: `docs/superpowers/specs/2026-07-06-deterministic-seed-generator-design.md`. Every behavioral rule below is copied from it verbatim; do not re-derive or re-litigate them mid-implementation.
- **Zero LLM calls, zero prompt generation, zero AI reasoning** anywhere in `deterministic_seed_generator.py`.
- **`entity_metadata.py` is the only parser** — do not add AST/regex parsing logic anywhere else, and do not modify `entity_metadata.py` (no changes needed for this feature).
- **No entity-name/keyword special-casing** anywhere (no "Priority", "Category", "Status", etc.) — every rule is structural (FK-target / type-based).
- **`TARGET_ROWS = 3`** rows per lookup entity, always.
- **Count-based idempotency only** (`db.query(Model).count() >= TARGET_ROWS`) — never a field-guess or `UNIQUE`-constraint-based check.
- **No runtime lookups against out-of-graph entities.** A candidate with a required FK — direct, transitive, or self-referential — to anything outside the eligible lookup set is excluded (logged), not resolved with a live query.
- **Every failure path falls back to today's exact static stub** (the 4-line `{'seeded': True, 'message': 'Demo data ready'}` template already in `v6_orchestrator.py`) — this feature must never make `seed_routes.py` generation fail outright.
- **Scope lock**: only `v6_orchestrator.py`'s missing-`seed_routes.py` fallback branch changes behavior. `shared_contract.py`'s primary LLM-authored seed prompt is untouched.
- **pytest is not installed** in `backend/venv` — do not add it as a dependency. Tests are plain scripts using `assert` and a bottom-of-file runner (pattern shown in Task 1).
- Interpreter for all commands below: `backend/venv/Scripts/python.exe` (Windows venv), run from the repo root unless stated otherwise.

---

## Task 1: Discovery, lookup candidacy, transitive eligibility, topological ordering

**Files:**
- Create: `backend/app/services/deterministic_seed_generator.py`
- Test: `backend/tests/adr002/test_discovery_and_ordering.py`

**Interfaces:**
- Consumes: `backend/app/services/entity_metadata.py` — `EntityDefinition`, `FieldDefinition`, `extract_entity_definition(model_content: str, source_path: str = "") -> EntityDefinition | None` (existing, unchanged, Experiment 017).
- Produces:
  - `discover_models(project_path: str) -> dict[str, EntityDefinition]`
  - `find_lookup_entities(entities: dict[str, EntityDefinition]) -> tuple[list[EntityDefinition], list[str]]` (returns `(eligible_entities, exclusion_log_lines)`)
  - `topological_order(entities: list[EntityDefinition]) -> list[EntityDefinition] | None` (returns `None` on cycle)

- [ ] **Step 1: Write the failing test file**

Create `backend/tests/adr002/test_discovery_and_ordering.py`:

```python
"""
Unit tests for deterministic_seed_generator's discovery, candidacy,
eligibility, and ordering logic. Plain assert-based (no pytest installed
in this project) -- run directly: python tests/adr002/test_discovery_and_ordering.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.services.deterministic_seed_generator import (
    discover_models,
    find_lookup_entities,
    topological_order,
)
from app.services.entity_metadata import extract_entity_definition

FIXTURE_PRIORITY = '''
from sqlalchemy import Column, Integer, String
from app.database import Base

class Priority(Base):
    __tablename__ = "priorities"
    id = Column(Integer, primary_key=True)
    name = Column(String(50), nullable=False)
    level = Column(Integer, nullable=False)
'''

FIXTURE_TASK = '''
from sqlalchemy import Column, Integer, String, ForeignKey
from app.database import Base

class Task(Base):
    __tablename__ = "tasks"
    id = Column(Integer, primary_key=True)
    title = Column(String(200), nullable=False)
    priority_id = Column(Integer, ForeignKey("priorities.id"), nullable=False)
'''

FIXTURE_USER = '''
from sqlalchemy import Column, Integer, String
from app.database import Base

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    email = Column(String(255), nullable=False)
'''

FIXTURE_SHIM = 'from app.models.priorities import Priority\n'

FIXTURE_CATEGORY = '''
from sqlalchemy import Column, Integer, String
from app.database import Base

class Category(Base):
    __tablename__ = "categories"
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
'''

FIXTURE_SUBCATEGORY = '''
from sqlalchemy import Column, Integer, String, ForeignKey
from app.database import Base

class Subcategory(Base):
    __tablename__ = "subcategories"
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=False)
'''

FIXTURE_PRODUCT = '''
from sqlalchemy import Column, Integer, String, ForeignKey
from app.database import Base

class Product(Base):
    __tablename__ = "products"
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    subcategory_id = Column(Integer, ForeignKey("subcategories.id"), nullable=False)
'''

FIXTURE_TAG = '''
from sqlalchemy import Column, Integer, String, ForeignKey
from app.database import Base

class Tag(Base):
    __tablename__ = "tags"
    id = Column(Integer, primary_key=True)
    name = Column(String(50), nullable=False)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False)
'''

FIXTURE_POST = '''
from sqlalchemy import Column, Integer, String, ForeignKey
from app.database import Base

class Post(Base):
    __tablename__ = "posts"
    id = Column(Integer, primary_key=True)
    title = Column(String(200), nullable=False)
    tag_id = Column(Integer, ForeignKey("tags.id"), nullable=False)
'''

FIXTURE_STATUS = '''
from sqlalchemy import Column, Integer, String, ForeignKey
from app.database import Base

class Status(Base):
    __tablename__ = "statuses"
    id = Column(Integer, primary_key=True)
    label = Column(String(50), nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
'''

FIXTURE_TICKET = '''
from sqlalchemy import Column, Integer, String, ForeignKey
from app.database import Base

class Ticket(Base):
    __tablename__ = "tickets"
    id = Column(Integer, primary_key=True)
    subject = Column(String(200), nullable=False)
    status_id = Column(Integer, ForeignKey("statuses.id"), nullable=False)
'''

FIXTURE_NODE_SELF_REF = '''
from sqlalchemy import Column, Integer, String, ForeignKey
from app.database import Base

class Node(Base):
    __tablename__ = "nodes"
    id = Column(Integer, primary_key=True)
    label = Column(String(50), nullable=False)
    parent_id = Column(Integer, ForeignKey("nodes.id"), nullable=False)
'''

FIXTURE_ALPHA_CYCLE = '''
from sqlalchemy import Column, Integer, String, ForeignKey
from app.database import Base

class Alpha(Base):
    __tablename__ = "alphas"
    id = Column(Integer, primary_key=True)
    name = Column(String(50), nullable=False)
    beta_id = Column(Integer, ForeignKey("betas.id"), nullable=False)
'''

FIXTURE_BETA_CYCLE = '''
from sqlalchemy import Column, Integer, String, ForeignKey
from app.database import Base

class Beta(Base):
    __tablename__ = "betas"
    id = Column(Integer, primary_key=True)
    name = Column(String(50), nullable=False)
    alpha_id = Column(Integer, ForeignKey("alphas.id"), nullable=False)
'''


def _entities(*sources: str) -> dict:
    out = {}
    for src in sources:
        e = extract_entity_definition(src)
        if e is not None:
            out[e.table_name] = e
    return out


def test_discover_models_basic():
    with tempfile.TemporaryDirectory() as tmp:
        models_dir = os.path.join(tmp, "app", "models")
        os.makedirs(models_dir)
        with open(os.path.join(models_dir, "priority.py"), "w") as f:
            f.write(FIXTURE_PRIORITY)
        with open(os.path.join(models_dir, "task.py"), "w") as f:
            f.write(FIXTURE_TASK)
        entities = discover_models(tmp)
        assert set(entities.keys()) == {"priorities", "tasks"}, entities.keys()


def test_discover_models_skips_shim():
    with tempfile.TemporaryDirectory() as tmp:
        models_dir = os.path.join(tmp, "app", "models")
        os.makedirs(models_dir)
        with open(os.path.join(models_dir, "priorities.py"), "w") as f:
            f.write(FIXTURE_PRIORITY)
        with open(os.path.join(models_dir, "priority.py"), "w") as f:
            f.write(FIXTURE_SHIM)
        entities = discover_models(tmp)
        assert list(entities.keys()) == ["priorities"]


def test_discover_models_excludes_users():
    with tempfile.TemporaryDirectory() as tmp:
        models_dir = os.path.join(tmp, "app", "models")
        os.makedirs(models_dir)
        with open(os.path.join(models_dir, "user.py"), "w") as f:
            f.write(FIXTURE_USER)
        entities = discover_models(tmp)
        assert entities == {}


def test_discover_models_empty_project():
    with tempfile.TemporaryDirectory() as tmp:
        entities = discover_models(tmp)
        assert entities == {}


def test_find_lookup_entities_simple_fk_target():
    entities = _entities(FIXTURE_PRIORITY, FIXTURE_TASK)
    eligible, exclusions = find_lookup_entities(entities)
    names = {e.table_name for e in eligible}
    assert names == {"priorities"}, names
    assert exclusions == []


def test_find_lookup_entities_excludes_entity_with_no_inbound_fk():
    # Product has zero incoming FK -- it's the "business entity" and must
    # never become a candidate no matter how many FKs it declares outward.
    entities = _entities(FIXTURE_CATEGORY, FIXTURE_SUBCATEGORY, FIXTURE_PRODUCT)
    eligible, exclusions = find_lookup_entities(entities)
    names = {e.table_name for e in eligible}
    assert "products" not in names, names


def test_find_lookup_entities_multilevel_chain_eligible():
    entities = _entities(FIXTURE_CATEGORY, FIXTURE_SUBCATEGORY, FIXTURE_PRODUCT)
    eligible, exclusions = find_lookup_entities(entities)
    names = {e.table_name for e in eligible}
    assert names == {"categories", "subcategories"}, names
    assert exclusions == []


def test_find_lookup_entities_excludes_required_external_fk():
    # Tag is a candidate (Post FKs to it) but requires a real `users` row --
    # must be excluded, not resolved with a runtime lookup.
    entities = _entities(FIXTURE_TAG, FIXTURE_POST, FIXTURE_USER)
    eligible, exclusions = find_lookup_entities(entities)
    names = {e.table_name for e in eligible}
    assert "tags" not in names, names
    assert any("tags" in line and "outside the deterministic lookup graph" in line
               for line in exclusions), exclusions


def test_find_lookup_entities_nullable_external_fk_stays_eligible():
    # Status has a nullable FK to users -- must NOT be excluded, and the
    # nullable column is simply omitted later at render time (Task 2).
    entities = _entities(FIXTURE_STATUS, FIXTURE_TICKET, FIXTURE_USER)
    eligible, exclusions = find_lookup_entities(entities)
    names = {e.table_name for e in eligible}
    assert "statuses" in names, names
    assert exclusions == []


def test_find_lookup_entities_excludes_required_self_reference():
    # Node's own parent_id -> nodes.id makes "nodes" a candidate (it's an
    # FK target of itself); a REQUIRED self-reference can never be
    # satisfied by the first inserted row and must be excluded.
    entities = _entities(FIXTURE_NODE_SELF_REF)
    eligible, exclusions = find_lookup_entities(entities)
    names = {e.table_name for e in eligible}
    assert "nodes" not in names, names
    assert any("self-referential" in line for line in exclusions), exclusions


def test_topological_order_multilevel_chain():
    entities = _entities(FIXTURE_CATEGORY, FIXTURE_SUBCATEGORY, FIXTURE_PRODUCT)
    eligible, _ = find_lookup_entities(entities)
    ordered = topological_order(eligible)
    assert ordered is not None
    order_names = [e.table_name for e in ordered]
    assert order_names.index("categories") < order_names.index("subcategories"), order_names


def test_topological_order_detects_cycle():
    entities = _entities(FIXTURE_ALPHA_CYCLE, FIXTURE_BETA_CYCLE)
    eligible, exclusions = find_lookup_entities(entities)
    # Neither required FK points outside the pair, so eligibility keeps both --
    # the cycle can only be caught by topological_order.
    names = {e.table_name for e in eligible}
    assert names == {"alphas", "betas"}, names
    ordered = topological_order(eligible)
    assert ordered is None


if __name__ == "__main__":
    import traceback
    tests = [obj for name, obj in list(globals().items()) if name.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS: {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL: {t.__name__}: {e}")
        except Exception:
            failed += 1
            print(f"ERROR: {t.__name__}:")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    raise SystemExit(1 if failed else 0)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `backend/venv/Scripts/python.exe backend/tests/adr002/test_discovery_and_ordering.py`
Expected: `ModuleNotFoundError: No module named 'app.services.deterministic_seed_generator'`

- [ ] **Step 3: Write the implementation**

Create `backend/app/services/deterministic_seed_generator.py`:

```python
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
    all_entities = list(entities.values())

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
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `backend/venv/Scripts/python.exe backend/tests/adr002/test_discovery_and_ordering.py`
Expected: `13/13 passed` (all `PASS:` lines, exit code 0)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/deterministic_seed_generator.py backend/tests/adr002/test_discovery_and_ordering.py
git commit -m "Add ADR-002 seed generator: discovery, lookup candidacy, eligibility, ordering

Unwired, unused by any live code path -- pure addition, zero behavior
change to the running pipeline. Implements the frozen spec's FK-target
candidacy + transitive required-FK eligibility rule and Kahn's-algorithm
topological ordering with explicit cycle detection.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 2: Type-driven value generation and source rendering

**Files:**
- Modify: `backend/app/services/deterministic_seed_generator.py`
- Test: `backend/tests/adr002/test_render.py`

**Interfaces:**
- Consumes: `EntityDefinition`, `FieldDefinition` (Task 1's discovery output); `TARGET_ROWS`, `GENERATOR_VERSION` (Task 1 constants).
- Produces:
  - `generate_field_value(field: FieldDefinition, class_name: str, loop_var: str = "i") -> str` — returns a Python **source expression string** referencing the runtime loop variable (not a fixed literal — see implementation note below).
  - `render_seed_routes(entities_in_order: list) -> str` — full `seed_routes.py` source.

**Implementation note (reconciles spec wording with the Idempotency section's own pseudocode):** the frozen spec's Idempotency section already shows `for i in range(existing, TARGET_ROWS): ... deterministic literals ...` — a **runtime loop**, because `existing` (from `db.query(Model).count()`) isn't known until the generated code actually executes. `generate_field_value` therefore emits an *expression* in terms of the loop variable `i` (e.g. `"(i + 1)"`, `'"ClassName " + str(i + 1)'`), not a precomputed literal for a fixed row index — this is the only way per-row values can vary correctly starting from an arbitrary `existing` count. This doesn't change any observable behavior described in the spec, only how the "row_index" concept is realized in generated code.

**Test coverage note:** this task's test file also covers the frozen spec's testing-plan items 7 ("Repeated `POST /seed` semantics: first call inserts 3, second call inserts 0") and 8 ("Pre-existing data: entity already has 1-2 rows before first `/seed` call → generator tops up to `TARGET_ROWS`") by actually executing the rendered source against a real in-memory SQLite database, not just asserting on the generated text — the earlier structural tests alone (parses, contains keywords) can't prove the count-based idempotency logic is actually correct at runtime.

- [ ] **Step 1: Write the failing test file**

Create `backend/tests/adr002/test_render.py`:

```python
"""
Unit tests for deterministic_seed_generator's value generation and
source rendering. Plain assert-based -- run directly:
python tests/adr002/test_render.py
"""
import ast
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.services.deterministic_seed_generator import (
    find_lookup_entities,
    generate_field_value,
    render_seed_routes,
    topological_order,
)
from app.services.entity_metadata import extract_entity_definition

FIXTURE_PRIORITY = '''
from sqlalchemy import Column, Integer, String
from app.database import Base

class Priority(Base):
    __tablename__ = "priorities"
    id = Column(Integer, primary_key=True)
    name = Column(String(50), nullable=False)
    level = Column(Integer, nullable=False)
    is_default = Column(Boolean, nullable=False)
    created_at = Column(DateTime, nullable=False)
    external_ref = Column(UUID, nullable=False)
'''

FIXTURE_TASK = '''
from sqlalchemy import Column, Integer, String, ForeignKey
from app.database import Base

class Task(Base):
    __tablename__ = "tasks"
    id = Column(Integer, primary_key=True)
    title = Column(String(200), nullable=False)
    priority_id = Column(Integer, ForeignKey("priorities.id"), nullable=False)
'''

FIXTURE_CATEGORY = '''
from sqlalchemy import Column, Integer, String
from app.database import Base

class Category(Base):
    __tablename__ = "categories"
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
'''

FIXTURE_SUBCATEGORY = '''
from sqlalchemy import Column, Integer, String, ForeignKey
from app.database import Base

class Subcategory(Base):
    __tablename__ = "subcategories"
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=False)
'''

FIXTURE_PRODUCT = '''
from sqlalchemy import Column, Integer, String, ForeignKey
from app.database import Base

class Product(Base):
    __tablename__ = "products"
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    subcategory_id = Column(Integer, ForeignKey("subcategories.id"), nullable=False)
'''

FIXTURE_STATUS = '''
from sqlalchemy import Column, Integer, String, ForeignKey
from app.database import Base

class Status(Base):
    __tablename__ = "statuses"
    id = Column(Integer, primary_key=True)
    label = Column(String(50), nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
'''

FIXTURE_TICKET = '''
from sqlalchemy import Column, Integer, String, ForeignKey
from app.database import Base

class Ticket(Base):
    __tablename__ = "tickets"
    id = Column(Integer, primary_key=True)
    subject = Column(String(200), nullable=False)
    status_id = Column(Integer, ForeignKey("statuses.id"), nullable=False)
'''


def _entities(*sources):
    out = {}
    for src in sources:
        e = extract_entity_definition(src)
        if e is not None:
            out[e.table_name] = e
    return out


def test_generate_field_value_string_uses_loop_var():
    entities = _entities(FIXTURE_PRIORITY)
    entity = entities["priorities"]
    name_field = next(f for f in entity.fields if f.name == "name")
    expr = generate_field_value(name_field, class_name="Priority")
    assert "i" in expr and "Priority" in expr, expr


def test_generate_field_value_int():
    entities = _entities(FIXTURE_PRIORITY)
    entity = entities["priorities"]
    level_field = next(f for f in entity.fields if f.name == "level")
    expr = generate_field_value(level_field, class_name="Priority")
    assert expr == "(i + 1)", expr


def test_generate_field_value_bool():
    entities = _entities(FIXTURE_PRIORITY)
    entity = entities["priorities"]
    bool_field = next(f for f in entity.fields if f.name == "is_default")
    expr = generate_field_value(bool_field, class_name="Priority")
    assert expr == "(i % 2 == 0)", expr


def test_generate_field_value_date():
    entities = _entities(FIXTURE_PRIORITY)
    entity = entities["priorities"]
    date_field = next(f for f in entity.fields if f.name == "created_at")
    expr = generate_field_value(date_field, class_name="Priority")
    assert "timedelta" in expr and "i" in expr, expr


def test_generate_field_value_uuid():
    entities = _entities(FIXTURE_PRIORITY)
    entity = entities["priorities"]
    uuid_field = next(f for f in entity.fields if f.name == "external_ref")
    expr = generate_field_value(uuid_field, class_name="Priority")
    assert expr == "str(uuid.uuid4())", expr


def test_render_single_lookup_entity_parses_and_has_version_header():
    entities = _entities(FIXTURE_PRIORITY, FIXTURE_TASK)
    eligible, _ = find_lookup_entities(entities)
    ordered = topological_order(eligible)
    source = render_seed_routes(ordered)
    ast.parse(source)  # must be syntactically valid Python
    assert "# Generated by ForgeAI Deterministic Seeder" in source
    assert "# ADR-002" in source
    assert "Generator Version: 1" in source
    assert "def _seed_priorities(db" in source
    assert "@seed_router.post('/seed')" in source


def test_render_multilevel_chain_parses_and_orders_helpers():
    entities = _entities(FIXTURE_CATEGORY, FIXTURE_SUBCATEGORY, FIXTURE_PRODUCT)
    eligible, _ = find_lookup_entities(entities)
    ordered = topological_order(eligible)
    source = render_seed_routes(ordered)
    ast.parse(source)
    cat_def = source.index("def _seed_categories(")
    sub_def = source.index("def _seed_subcategories(")
    assert cat_def < sub_def, "categories helper must be defined before subcategories"
    # Subcategory's helper must query the already-seeded Category pool,
    # not attempt a runtime lookup against an out-of-graph entity.
    assert "db.query(Category).all()" in source


def test_render_nullable_external_fk_omitted_from_insert():
    entities = _entities(FIXTURE_STATUS, FIXTURE_TICKET)
    eligible, _ = find_lookup_entities(entities)
    ordered = topological_order(eligible)
    source = render_seed_routes(ordered)
    ast.parse(source)
    # created_by (nullable FK to users, which isn't in the graph at all
    # here) must never appear as a constructor kwarg.
    seed_status_fn = source[source.index("def _seed_statuses("):]
    assert "created_by=" not in seed_status_fn.split("def _seed_")[0]


# ── Execution-based tests (spec testing-plan items 7 & 8): prove the
# rendered code actually behaves idempotently when run against a real
# database, not just that it parses/contains the right keywords. Uses a
# real in-memory SQLite engine -- still $0, local, no LLM/server.
FIXTURE_PRIORITY_EXECUTABLE = '''
from sqlalchemy import Column, Integer, String
from app.database import Base

class Priority(Base):
    __tablename__ = "priorities"
    id = Column(Integer, primary_key=True)
    name = Column(String(50), nullable=False)
    level = Column(Integer, nullable=False)
'''

FIXTURE_TASK_EXECUTABLE = '''
from sqlalchemy import Column, Integer, String, ForeignKey
from app.database import Base

class Task(Base):
    __tablename__ = "tasks"
    id = Column(Integer, primary_key=True)
    title = Column(String(200), nullable=False)
    priority_id = Column(Integer, ForeignKey("priorities.id"), nullable=False)
'''


def _build_and_import_seed_module(tmp: str, model_sources: dict, rendered_source: str):
    """Writes a minimal REAL project (app/database.py wired to an
    in-memory SQLite engine, app/models/*.py from model_sources,
    app/routes/seed_routes.py = rendered_source) to `tmp`, adds it to
    sys.path, and imports the generated seed_routes module so its
    functions can be called directly against a real database."""
    import importlib

    app_dir = os.path.join(tmp, "app")
    models_dir = os.path.join(app_dir, "models")
    routes_dir = os.path.join(app_dir, "routes")
    os.makedirs(models_dir, exist_ok=True)
    os.makedirs(routes_dir, exist_ok=True)
    open(os.path.join(app_dir, "__init__.py"), "w").close()
    open(os.path.join(models_dir, "__init__.py"), "w").close()
    open(os.path.join(routes_dir, "__init__.py"), "w").close()

    with open(os.path.join(app_dir, "database.py"), "w") as f:
        f.write(
            "from sqlalchemy import create_engine\n"
            "from sqlalchemy.orm import sessionmaker, declarative_base\n\n"
            'engine = create_engine("sqlite:///:memory:", '
            'connect_args={"check_same_thread": False})\n'
            "SessionLocal = sessionmaker(bind=engine)\n"
            "Base = declarative_base()\n\n"
            "def get_db():\n"
            "    db = SessionLocal()\n"
            "    try:\n"
            "        yield db\n"
            "    finally:\n"
            "        db.close()\n"
        )

    for fname, content in model_sources.items():
        with open(os.path.join(models_dir, fname), "w") as f:
            f.write(content)

    with open(os.path.join(routes_dir, "seed_routes.py"), "w") as f:
        f.write(rendered_source)

    sys.path.insert(0, tmp)
    for mod_name in list(sys.modules):
        if mod_name == "app" or mod_name.startswith("app."):
            del sys.modules[mod_name]
    db_module = importlib.import_module("app.database")
    db_module.Base.metadata.create_all(db_module.engine)
    seed_module = importlib.import_module("app.routes.seed_routes")
    return db_module, seed_module


def test_generated_seed_function_is_idempotent_end_to_end():
    import tempfile

    entities = _entities(FIXTURE_PRIORITY_EXECUTABLE, FIXTURE_TASK_EXECUTABLE)
    eligible, _ = find_lookup_entities(entities)
    ordered = topological_order(eligible)
    source = render_seed_routes(ordered)

    with tempfile.TemporaryDirectory() as tmp:
        db_module, seed_module = _build_and_import_seed_module(
            tmp,
            {"priority.py": FIXTURE_PRIORITY_EXECUTABLE, "task.py": FIXTURE_TASK_EXECUTABLE},
            source,
        )
        session = db_module.SessionLocal()
        try:
            first = seed_module._seed_priorities(session)
            assert first == {"inserted": 3, "skipped": 0, "already_existed": 0}, first
            second = seed_module._seed_priorities(session)
            assert second == {"inserted": 0, "skipped": 3, "already_existed": 3}, second
        finally:
            session.close()
            sys.path.remove(tmp)


def test_generated_seed_function_tops_up_partial_existing_data():
    import importlib
    import tempfile

    entities = _entities(FIXTURE_PRIORITY_EXECUTABLE, FIXTURE_TASK_EXECUTABLE)
    eligible, _ = find_lookup_entities(entities)
    ordered = topological_order(eligible)
    source = render_seed_routes(ordered)

    with tempfile.TemporaryDirectory() as tmp:
        db_module, seed_module = _build_and_import_seed_module(
            tmp,
            {"priority.py": FIXTURE_PRIORITY_EXECUTABLE, "task.py": FIXTURE_TASK_EXECUTABLE},
            source,
        )
        priority_module = importlib.import_module("app.models.priority")
        session = db_module.SessionLocal()
        try:
            session.add(priority_module.Priority(name="Pre-existing", level=1))
            session.commit()
            result = seed_module._seed_priorities(session)
            assert result == {"inserted": 2, "skipped": 0, "already_existed": 1}, result
            assert session.query(priority_module.Priority).count() == 3
        finally:
            session.close()
            sys.path.remove(tmp)


if __name__ == "__main__":
    import traceback
    tests = [obj for name, obj in list(globals().items()) if name.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS: {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL: {t.__name__}: {e}")
        except Exception:
            failed += 1
            print(f"ERROR: {t.__name__}:")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    raise SystemExit(1 if failed else 0)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `backend/venv/Scripts/python.exe backend/tests/adr002/test_render.py`
Expected: `ImportError: cannot import name 'generate_field_value' from 'app.services.deterministic_seed_generator'`

- [ ] **Step 3: Write the implementation**

Append to `backend/app/services/deterministic_seed_generator.py`:

```python
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
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `backend/venv/Scripts/python.exe backend/tests/adr002/test_render.py`
Expected: `11/11 passed`

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/deterministic_seed_generator.py backend/tests/adr002/test_render.py
git commit -m "Add ADR-002 seed generator: typed value generation + source rendering

Still unwired. render_seed_routes() produces complete, ast.parse-clean
seed_routes.py source: version header, count-gated per-entity helpers,
internal FK-chain support via a runtime pool query against already-seeded
sibling rows (never against an out-of-graph entity), nullable external
FKs simply omitted from the insert.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 3: Top-level `generate()` orchestration with the single fallback boundary

**Files:**
- Modify: `backend/app/services/deterministic_seed_generator.py`
- Test: `backend/tests/adr002/test_generate.py`

**Interfaces:**
- Consumes: `discover_models`, `find_lookup_entities`, `topological_order`, `render_seed_routes` (Tasks 1-2).
- Produces: `generate(project_path: str) -> tuple[str | None, dict]` — `(source_code_or_None, telemetry_dict)`. `telemetry` keys: `adr002_enabled` (bool), `entities_discovered` (int), `lookup_entities` (int), `fallback_used` (bool), `fallback_reason` (str), `generation_time_ms` (float), `exclusions` (list[str]). This is the ONLY function `v6_orchestrator.py` (Task 4) calls.

- [ ] **Step 1: Write the failing test file**

Create `backend/tests/adr002/test_generate.py`:

```python
"""
Unit tests for the top-level generate() orchestration and its fallback
boundary. Plain assert-based -- run directly:
python tests/adr002/test_generate.py
"""
import os
import sys
import tempfile
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.services import deterministic_seed_generator as gen

FIXTURE_PRIORITY = '''
from sqlalchemy import Column, Integer, String
from app.database import Base

class Priority(Base):
    __tablename__ = "priorities"
    id = Column(Integer, primary_key=True)
    name = Column(String(50), nullable=False)
'''

FIXTURE_TASK = '''
from sqlalchemy import Column, Integer, String, ForeignKey
from app.database import Base

class Task(Base):
    __tablename__ = "tasks"
    id = Column(Integer, primary_key=True)
    title = Column(String(200), nullable=False)
    priority_id = Column(Integer, ForeignKey("priorities.id"), nullable=False)
'''

FIXTURE_ALPHA_CYCLE = '''
from sqlalchemy import Column, Integer, String, ForeignKey
from app.database import Base

class Alpha(Base):
    __tablename__ = "alphas"
    id = Column(Integer, primary_key=True)
    name = Column(String(50), nullable=False)
    beta_id = Column(Integer, ForeignKey("betas.id"), nullable=False)
'''

FIXTURE_BETA_CYCLE = '''
from sqlalchemy import Column, Integer, String, ForeignKey
from app.database import Base

class Beta(Base):
    __tablename__ = "betas"
    id = Column(Integer, primary_key=True)
    name = Column(String(50), nullable=False)
    alpha_id = Column(Integer, ForeignKey("alphas.id"), nullable=False)
'''


def _project_with(tmp, files: dict) -> str:
    models_dir = os.path.join(tmp, "app", "models")
    os.makedirs(models_dir, exist_ok=True)
    for fname, content in files.items():
        with open(os.path.join(models_dir, fname), "w") as f:
            f.write(content)
    return tmp


def test_generate_success_returns_source_and_telemetry():
    with tempfile.TemporaryDirectory() as tmp:
        _project_with(tmp, {"priority.py": FIXTURE_PRIORITY, "task.py": FIXTURE_TASK})
        source, telemetry = gen.generate(tmp)
        assert source is not None
        assert telemetry["adr002_enabled"] is True
        assert telemetry["fallback_used"] is False
        assert telemetry["lookup_entities"] == 1
        assert telemetry["entities_discovered"] == 2
        assert telemetry["generation_time_ms"] >= 0


def test_generate_falls_back_on_empty_project():
    with tempfile.TemporaryDirectory() as tmp:
        source, telemetry = gen.generate(tmp)
        assert source is None
        assert telemetry["fallback_used"] is True
        assert telemetry["fallback_reason"] == "no models discovered"


def test_generate_falls_back_when_no_lookup_entities():
    with tempfile.TemporaryDirectory() as tmp:
        # Task has no incoming FK from anything -- zero lookup candidates.
        _project_with(tmp, {"task.py": '''
from sqlalchemy import Column, Integer, String
from app.database import Base

class Task(Base):
    __tablename__ = "tasks"
    id = Column(Integer, primary_key=True)
    title = Column(String(200), nullable=False)
'''})
        source, telemetry = gen.generate(tmp)
        assert source is None
        assert telemetry["fallback_reason"] == "no lookup entities"


def test_generate_falls_back_on_fk_cycle():
    with tempfile.TemporaryDirectory() as tmp:
        _project_with(tmp, {"alpha.py": FIXTURE_ALPHA_CYCLE, "beta.py": FIXTURE_BETA_CYCLE})
        source, telemetry = gen.generate(tmp)
        assert source is None
        assert telemetry["fallback_reason"] == "FK cycle detected"


def test_generate_never_raises_on_unexpected_exception():
    with tempfile.TemporaryDirectory() as tmp:
        _project_with(tmp, {"priority.py": FIXTURE_PRIORITY, "task.py": FIXTURE_TASK})
        with mock.patch.object(gen, "render_seed_routes", side_effect=RuntimeError("boom")):
            source, telemetry = gen.generate(tmp)
        assert source is None
        assert "boom" in telemetry["fallback_reason"]


if __name__ == "__main__":
    import traceback
    tests = [obj for name, obj in list(globals().items()) if name.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS: {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL: {t.__name__}: {e}")
        except Exception:
            failed += 1
            print(f"ERROR: {t.__name__}:")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    raise SystemExit(1 if failed else 0)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `backend/venv/Scripts/python.exe backend/tests/adr002/test_generate.py`
Expected: `AttributeError: module 'app.services.deterministic_seed_generator' has no attribute 'generate'`

- [ ] **Step 3: Write the implementation**

Append to `backend/app/services/deterministic_seed_generator.py`:

```python
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
            return None, telemetry

        eligible, exclusion_log = find_lookup_entities(entities)
        telemetry["exclusions"] = exclusion_log
        telemetry["lookup_entities"] = len(eligible)
        if not eligible:
            telemetry["fallback_reason"] = "no lookup entities"
            return None, telemetry

        ordered = topological_order(eligible)
        if ordered is None:
            telemetry["fallback_reason"] = "FK cycle detected"
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
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `backend/venv/Scripts/python.exe backend/tests/adr002/test_generate.py`
Expected: `5/5 passed`

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/deterministic_seed_generator.py backend/tests/adr002/test_generate.py
git commit -m "Add ADR-002 seed generator: generate() orchestration + fallback boundary

Still unwired -- no caller exists yet. generate(project_path) is the
single entry point v6_orchestrator.py will call in the next commit; every
fallback trigger (no models, no lookup entities, FK cycle, any
exception) is exercised and returns (None, telemetry) rather than
raising.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 4: Wire into `v6_orchestrator.py` — the one live-behavior commit

**Files:**
- Modify: `backend/app/services/v6_orchestrator.py:444-461`
- Test: `backend/tests/adr002/test_orchestrator_wiring.py`

**Interfaces:**
- Consumes: `app.services.deterministic_seed_generator.generate(project_path: str) -> tuple[str | None, dict]` (Task 3).
- Produces: no new public interface — this task only changes `v6_orchestrator.py`'s internal fallback branch behavior.

- [ ] **Step 1: Write the failing test file**

This test exercises the exact fallback branch in isolation by re-creating its logic path against a temp directory (the surrounding function in `v6_orchestrator.py` is large and network/LLM-dependent; we test the extracted decision logic, which is what actually changes).

Create `backend/tests/adr002/test_orchestrator_wiring.py`:

```python
"""
Verifies v6_orchestrator.py's missing-seed_routes.py branch calls the
ADR-002 generator and falls back correctly. Reads the live source of
v6_orchestrator.py and asserts the wiring is present and correctly
ordered (generate() called, static stub only written when it returns
None) -- a lightweight structural check rather than executing the whole
(large, LLM/network-dependent) surrounding function.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


def _read_orchestrator_source() -> str:
    path = os.path.join(
        os.path.dirname(__file__), "..", "..", "app", "services", "v6_orchestrator.py"
    )
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def test_orchestrator_imports_and_calls_generate():
    src = _read_orchestrator_source()
    seed_block_start = src.index('filepath in ("app/routes/seed_routes.py"')
    seed_block = src[seed_block_start:seed_block_start + 2000]
    assert "deterministic_seed_generator" in seed_block
    assert "generate(project_path)" in seed_block


def test_orchestrator_still_writes_static_stub_on_none():
    src = _read_orchestrator_source()
    seed_block_start = src.index('filepath in ("app/routes/seed_routes.py"')
    seed_block = src[seed_block_start:seed_block_start + 2000]
    assert "Demo data ready" in seed_block, "static-stub fallback text must still be present"


def test_generate_actually_wired_end_to_end():
    # Exercise the real generate() call the orchestrator now makes,
    # against a temp project, to prove the import path/signature match.
    import tempfile
    from app.services.deterministic_seed_generator import generate

    with tempfile.TemporaryDirectory() as tmp:
        models_dir = os.path.join(tmp, "app", "models")
        os.makedirs(models_dir)
        with open(os.path.join(models_dir, "priority.py"), "w") as f:
            f.write('''
from sqlalchemy import Column, Integer, String
from app.database import Base

class Priority(Base):
    __tablename__ = "priorities"
    id = Column(Integer, primary_key=True)
    name = Column(String(50), nullable=False)
''')
        with open(os.path.join(models_dir, "task.py"), "w") as f:
            f.write('''
from sqlalchemy import Column, Integer, String, ForeignKey
from app.database import Base

class Task(Base):
    __tablename__ = "tasks"
    id = Column(Integer, primary_key=True)
    title = Column(String(200), nullable=False)
    priority_id = Column(Integer, ForeignKey("priorities.id"), nullable=False)
''')
        source, telemetry = generate(tmp)
        assert source is not None
        assert telemetry["fallback_used"] is False


if __name__ == "__main__":
    import traceback
    tests = [obj for name, obj in list(globals().items()) if name.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS: {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL: {t.__name__}: {e}")
        except Exception:
            failed += 1
            print(f"ERROR: {t.__name__}:")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    raise SystemExit(1 if failed else 0)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `backend/venv/Scripts/python.exe backend/tests/adr002/test_orchestrator_wiring.py`
Expected: `FAIL: test_orchestrator_imports_and_calls_generate` (the wiring doesn't exist yet — `deterministic_seed_generator` not found in the seed block)

- [ ] **Step 3: Write the implementation**

In `backend/app/services/v6_orchestrator.py`, replace lines 444-461 (the `if not os.path.exists(abs_path):` block's `seed_routes.py` special case):

```python
                if not os.path.exists(abs_path):
                    # seed_routes.py: never call the LLM -- it generates wrong-project content
                    # (gym/hospital models) because it has no project context. Try the
                    # deterministic ADR-002 seeder first (reuses entity_metadata.py to seed
                    # real lookup/reference tables); fall back to the static zero-insert
                    # stub only if that returns nothing usable. Never let this branch fail
                    # generation outright -- the static stub is always the safety net.
                    if filepath in ("app/routes/seed_routes.py", "app\\routes\\seed_routes.py"):
                        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
                        from app.services.deterministic_seed_generator import generate as _gen_seed
                        _source, _telemetry = _gen_seed(project_path)
                        if _source is not None:
                            with open(abs_path, "w", encoding="utf-8") as _sf:
                                _sf.write(_source)
                            print(
                                f"  [patcher] ADR-002 deterministic seed_routes.py generated "
                                f"({_telemetry['lookup_entities']} lookup entities, "
                                f"{_telemetry['generation_time_ms']}ms)"
                            )
                            for _line in _telemetry["exclusions"]:
                                print(f"  [patcher]   {_line}")
                        else:
                            with open(abs_path, "w", encoding="utf-8") as _sf:
                                _sf.write(
                                    "from fastapi import APIRouter, Depends\n"
                                    "from sqlalchemy.orm import Session\n"
                                    "from app.database import get_db\n\n"
                                    "seed_router = APIRouter()\n\n"
                                    "@seed_router.post('/seed')\n"
                                    "def seed_data(db: Session = Depends(get_db)):\n"
                                    "    return {'seeded': True, 'message': 'Demo data ready'}\n"
                                )
                            print(
                                f"  [patcher] Generated minimal seed_routes.py stub "
                                f"(ADR-002 fallback: {_telemetry['fallback_reason']})"
                            )
                        continue
                    fix = generate_missing_file(filepath, "\n".join(file_errors), provider, project_path=project_path)
                    _llm["repairs"] += 1
                    if fix and fix.get("content"):
                        fix["path"] = _sanitize_path(fix["path"])
                        write_fix(project_path, fix)
                        save_fix_log(project_path, "Missing File", fix)
                    continue
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `backend/venv/Scripts/python.exe backend/tests/adr002/test_orchestrator_wiring.py`
Expected: `3/3 passed`

Also sanity-check the modified file still compiles:
Run: `backend/venv/Scripts/python.exe -c "import ast; ast.parse(open('backend/app/services/v6_orchestrator.py', encoding='utf-8').read())"`
Expected: no output, exit code 0

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/v6_orchestrator.py backend/tests/adr002/test_orchestrator_wiring.py
git commit -m "Wire ADR-002 deterministic seeder into v6_orchestrator.py's seed fallback

The one commit in this feature that changes live pipeline behavior.
When the LLM omits seed_routes.py, try the deterministic generator
first; fall back to today's exact static stub only if it returns None
(no models, no lookup entities, FK cycle, or any exception). Trivially
revertible: reverting this single commit restores static-stub-only
behavior with no other code depending on the new module.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 5: Telemetry — runtime seed counts surfaced in journey/canary output

**Files:**
- Modify: `backend/app/runtime/user_journey_runner.py:24-33` (JourneyResult dataclass), `:468-487` (best-effort `/seed` call)
- Modify: `backend/app/runtime/backend_runner.py:263-276` (journey_data construction)
- Test: `backend/tests/adr002/test_journey_seed_telemetry.py`

**Interfaces:**
- Consumes: `JourneyResult` dataclass (existing, `backend/app/runtime/user_journey_runner.py:24`).
- Produces: `JourneyResult.seed_summary: dict` (new field, default `{}`) — populated from the generated `POST /seed` endpoint's JSON response body (`response["summary"]`, the per-entity `{table: {inserted, skipped, already_existed}}` dict Task 2's `render_seed_routes` already emits).

- [ ] **Step 1: Write the failing test file**

Create `backend/tests/adr002/test_journey_seed_telemetry.py`:

```python
"""
Verifies JourneyResult carries structured seed telemetry captured from
the journey runner's existing best-effort POST /seed call, and that
backend_runner.py surfaces it in journey_data. Plain assert-based --
run directly: python tests/adr002/test_journey_seed_telemetry.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.runtime.user_journey_runner import JourneyResult


def test_journey_result_has_seed_summary_field_defaulting_empty():
    result = JourneyResult(success=True)
    assert result.seed_summary == {}


def test_journey_result_accepts_seed_summary():
    result = JourneyResult(success=True, seed_summary={"priorities": {"inserted": 3}})
    assert result.seed_summary == {"priorities": {"inserted": 3}}


def test_backend_runner_surfaces_seed_summary_in_journey_data():
    src_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "app", "runtime", "backend_runner.py"
    )
    with open(src_path, "r", encoding="utf-8") as f:
        src = f.read()
    block_start = src.index('journey_data = {')
    block = src[block_start:block_start + 800]
    assert "seed_summary" in block


def test_journey_runner_captures_seed_response_json():
    src_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "app", "runtime", "user_journey_runner.py"
    )
    with open(src_path, "r", encoding="utf-8") as f:
        src = f.read()
    seed_block_start = src.index("Reference-data seed")
    seed_block = src[seed_block_start:seed_block_start + 1200]
    assert "seed_summary" in seed_block
    assert ".json()" in seed_block


if __name__ == "__main__":
    import traceback
    tests = [obj for name, obj in list(globals().items()) if name.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS: {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL: {t.__name__}: {e}")
        except Exception:
            failed += 1
            print(f"ERROR: {t.__name__}:")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    raise SystemExit(1 if failed else 0)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `backend/venv/Scripts/python.exe backend/tests/adr002/test_journey_seed_telemetry.py`
Expected: `FAIL: test_journey_result_has_seed_summary_field_defaulting_empty` (`AttributeError: 'JourneyResult' object has no attribute 'seed_summary'`) and 3 more failures.

- [ ] **Step 3: Write the implementation**

In `backend/app/runtime/user_journey_runner.py`, modify the `JourneyResult` dataclass (currently lines 23-33):

```python
@dataclass
class JourneyResult:
    success: bool
    steps: list = field(default_factory=list)
    total_duration: float = 0.0
    persistence_verified: bool = False
    steps_passed: int = 0
    steps_failed: int = 0
    skipped: bool = False
    skip_reason: str = ""
    entity: str = ""
    seed_summary: dict = field(default_factory=dict)
```

Then replace the best-effort seed call (currently lines 484-487):

```python
    try:
        requests.post(f"{base}/seed", headers=headers, timeout=5)
    except Exception:
        pass
```

with:

```python
    seed_summary: dict = {}
    try:
        _seed_resp = requests.post(f"{base}/seed", headers=headers, timeout=5)
        _seed_json = _seed_resp.json()
        if isinstance(_seed_json, dict) and isinstance(_seed_json.get("summary"), dict):
            seed_summary = _seed_json["summary"]
    except Exception:
        pass
```

Then update the final `return JourneyResult(...)` (currently lines 848-856) to include the new field:

```python
    return JourneyResult(
        success=success,
        steps=steps,
        total_duration=round(time.time() - t0, 2),
        persistence_verified=persistence_verified,
        steps_passed=passed,
        steps_failed=failed,
        entity=entity,
        seed_summary=seed_summary,
    )
```

In `backend/app/runtime/backend_runner.py`, add `seed_summary` to the `journey_data` dict (currently lines 263-276):

```python
                journey_data = {
                    "success": journey_result.success,
                    "steps_passed": journey_result.steps_passed,
                    "steps_failed": journey_result.steps_failed,
                    "persistence_verified": journey_result.persistence_verified,
                    "skipped": journey_result.skipped,
                    "skip_reason": getattr(journey_result, "skip_reason", ""),
                    "entity": getattr(journey_result, "entity", ""),
                    "seed_summary": getattr(journey_result, "seed_summary", {}),
                    "steps": [
                        {"name": s.name, "passed": s.passed, "detail": s.detail}
                        for s in journey_result.steps
                    ],
                    "total_duration": journey_result.total_duration,
                }
```

Also print it when present, right after the existing journey pass/fail print block (after the `for step in journey_result.steps:` loop, currently ending at line 290):

```python
                    if journey_result.seed_summary:
                        print(f"  Seed summary: {journey_result.seed_summary}")
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `backend/venv/Scripts/python.exe backend/tests/adr002/test_journey_seed_telemetry.py`
Expected: `4/4 passed`

Also sanity-check both modified files still compile:
Run: `backend/venv/Scripts/python.exe -c "import ast; [ast.parse(open(p, encoding='utf-8').read()) for p in ['backend/app/runtime/user_journey_runner.py', 'backend/app/runtime/backend_runner.py']]"`
Expected: no output, exit code 0

- [ ] **Step 5: Commit**

```bash
git add backend/app/runtime/user_journey_runner.py backend/app/runtime/backend_runner.py backend/tests/adr002/test_journey_seed_telemetry.py
git commit -m "Surface deterministic-seeder runtime counts in journey telemetry

JourneyResult gains seed_summary (populated from the generated POST
/seed response's structured per-entity counts, which the ADR-002
renderer already emits) and backend_runner.py's journey_data /
console output now include it -- no new capture mechanism, reuses the
journey runner's existing best-effort /seed call.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 6: Canary benchmark and honest experiments.md entry

**Files:**
- Read: `backend/benchmark_results/canary_history.json` (Experiment 019 entry, label `m1-seed-before-crud`)
- Modify: `experiments.md` (append new entry)
- No test file — this is a live benchmark run, not a unit-testable change.

- [ ] **Step 1: Run all local test suites once more as a pre-flight gate**

Run:
```bash
backend/venv/Scripts/python.exe backend/tests/adr002/test_discovery_and_ordering.py && \
backend/venv/Scripts/python.exe backend/tests/adr002/test_render.py && \
backend/venv/Scripts/python.exe backend/tests/adr002/test_generate.py && \
backend/venv/Scripts/python.exe backend/tests/adr002/test_orchestrator_wiring.py && \
backend/venv/Scripts/python.exe backend/tests/adr002/test_journey_seed_telemetry.py
```
Expected: all five report `N/N passed`, combined exit code 0. Do not proceed to the canary if any fail.

- [ ] **Step 2: Run the 3-app canary**

Run (from `backend/`): `python scripts/run_canary.py --label adr002-deterministic-seeder --no-deploy`

This takes several minutes (3 full generation+repair+verify cycles). Capture full stdout to a log file for inspection, e.g. redirect to `m2_canary_adr002_run.log` at the repo root (matching this session's existing `m1_canary_*.log` naming convention).

- [ ] **Step 3: Compare against Experiment 019's baseline and grep for ADR-002 telemetry lines**

Run:
```bash
backend/venv/Scripts/python.exe -c "
import json
d = json.load(open('backend/benchmark_results/canary_history.json'))
runs = d['runs']
baseline = next(r for r in runs if r['label'] == 'm1-seed-before-crud')
current = runs[-1]
for app_b, app_c in zip(baseline['results'], current['results']):
    print(app_b['app'], '| forge_score', app_b['forge_score'], '->', app_c['forge_score'],
          '| crud_ok', app_b['crud_ok'], '->', app_c['crud_ok'],
          '| fix_attempts', app_b['fix_attempts'], '->', app_c['fix_attempts'])
"
```

In the captured canary log, grep for the ADR-002 codegen-time log lines added in Task 4 (`[patcher] ADR-002 deterministic seed_routes.py generated` / `[patcher] Generated minimal seed_routes.py stub (ADR-002 fallback: ...)`) and the runtime seed-summary line added in Task 5 (`Seed summary: ...`) to confirm whether the deterministic path actually fired this run — remember this is a fallback-path fix, so it only produces an observable effect if the LLM omitted `seed_routes.py` for at least one app this run (same caveat as Experiment 019).

- [ ] **Step 4: Write the honest experiments.md entry**

Append a new entry to `experiments.md` following this run's actual established format (see the existing Experiment 019 entry for tone/structure — lead with what was tested, what the telemetry actually showed, whether the mechanism fired, and the acceptance-criteria checklist from the frozen spec). Do not claim success if the fallback path never fired this run — report that as inconclusive and note the mechanism-level unit-test coverage (Tasks 1-3) as the evidence of correctness in that case, distinct from the aggregate benchmark signal.

- [ ] **Step 5: Commit**

```bash
git add experiments.md m2_canary_adr002_run.log
git commit -m "Log ADR-002 deterministic seeder canary run vs Experiment 019 baseline

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 7 (conditional): Write ADR-002

**Only proceed with this task if Task 6's benchmark confirms the frozen spec's acceptance criteria** (CRUD reliability improves or todo's specific gated failure disappears when the path fires; no new regressions in blog_cms/crm; repair count flat or down; telemetry confirms correct execution). If Task 6 is inconclusive (fallback path never fired this run), stop here and note in `experiments.md` that ADR-002 promotion awaits a confirming run where the path actually triggers — do not write the ADR on inconclusive evidence.

**Files:**
- Create: `docs/ADR-002-deterministic-reference-data-generation.md`

- [ ] **Step 1: Write the ADR**

Cover, per the frozen spec's "Post-implementation" section: Problem, Root Cause, Design, Alternatives Considered (field-guess vs. `UNIQUE`-constraint vs. count-based idempotency — why count-based won; runtime-lookup-with-skip vs. transitive-eligibility-exclusion for external FKs — why exclusion won), Trade-offs, Evidence (cite the specific unit test results from Tasks 1-5 and the Task 6 canary numbers), Benchmark Results, Rollback Strategy (revert Task 4's single commit; no other code depends on the new module), and Future Extensions (list only, do not implement): relationship/secondary-table seeding, composite lookup entities, Enum member-value extraction, advanced deterministic defaults.

- [ ] **Step 2: Commit**

```bash
git add docs/ADR-002-deterministic-reference-data-generation.md
git commit -m "Write ADR-002: deterministic reference-data generation

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```
