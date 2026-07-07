# ADR-001 Extension Investigation: Relationship/Association Extraction

**Status**: Investigation and specification only — no code written, no
`entity_metadata.py` changes, no ADR-004 created.
**Date**: 2026-07-07
**Method**: reviewed `entity_metadata.py` in full (unchanged since ADR-001/
002), grepped every currently-unzipped `generated_projects/*/app/models/`
directory for real `relationship()`/`secondary=`/`Enum(`/`Table(` usage
(rather than assuming shapes), and cross-referenced against
`patterns.json`'s `SQLAlchemyError`/`NoReferencedTableError`/
`RelationshipModelNotImported` entries and `shared_contract.py`'s existing
`relationship()` prompt rules.

---

## 1. Current `entity_metadata.py` review

`FieldDefinition` (name, sqlalchemy_type, nullable, has_default,
is_primary_key, is_foreign_key, fk_target) and `EntityDefinition`
(class_name, table_name, source_path, fields) are populated entirely by
`extract_entity_definition()`, which:
- Finds the **first** `class X(Base):` block via regex.
- Extracts `__tablename__` via regex.
- Extracts every `name = Column(...)` line via
  `_COLUMN_RE = re.compile(r'^\s+(\w+)\s*=\s*Column\(([^\n]*)\)\s*$')`.

**Structural limitation found, not relationship-specific**: `_COLUMN_RE`
requires the entire `Column(...)` call to fit on one line (`[^\n]*` plus a
`$` anchor). A `Column(...)` wrapped across multiple lines — common once a
column has several keyword arguments — would silently fail to match today,
independent of anything relationship-related. This is a pre-existing gap,
not introduced by this investigation, but relevant to the design below.

## 2. What relationship information is missing today

Grepped real generated output (`generated_projects/blogsphere/app/models/`)
rather than assuming shapes:

```python
# post.py
author = relationship("User", back_populates="posts")
comments = relationship("Comment", back_populates="post")
tags = relationship("Tag", secondary="post_tags", back_populates="posts")

# tag.py
posts = relationship("Post", secondary="post_tags", back_populates="tags")

# post_tags.py -- a bare module-level Table(), not a class
post_tags = Table(
    "post_tags", Base.metadata,
    Column("post_id", Integer, ForeignKey("posts.id"), primary_key=True),
    Column("tag_id", Integer, ForeignKey("tags.id"), primary_key=True),
)
```

None of this is captured today:
- `relationship(...)` calls — entirely invisible; `extract_entity_definition`
  only reads `Column(...)` lines.
- `back_populates` — the string that links two relationship declarations
  into one logical pair.
- `secondary=` — the association-table name string for many-to-many.
- **Bare `Table(...)` module-level constructs** — a structurally different
  shape than `class X(Base):`, invisible to the current regex entirely
  (it only looks for `class` blocks).
- **Composite primary keys** — `post_tags`'s two `primary_key=True`
  columns jointly form one; nothing today recognizes "more than one PK
  column on the same table" as a distinct concept.
- `Enum` column member values — `sqlalchemy_type` currently captures only
  the bare type-name token (e.g. the literal string `"Enum"`), never the
  member values.
- **`Enum(...)` usage was not found anywhere** in currently-unzipped
  `generated_projects/`, across any app — the LLM's actual observed
  behavior is plain `String` columns with implied choices (e.g. `Post.status
  = Column(String(20), nullable=False)`, not `Column(Enum(...))`). This is
  real negative evidence: Enum extraction, while cheap to add, has near-zero
  demonstrated payoff against what this codebase's generations actually do
  today.

## 3. What's deterministically extractable

| Construct | Deterministic? | Notes |
|---|---|---|
| `relationship("Target", ...)` target class | Yes | Always a string literal in every example found — same "structural, not semantic" extraction ADR-001 already relies on |
| `back_populates="..."` | Yes | String literal keyword arg |
| `secondary="..."` | Yes | String literal keyword arg |
| Bare `Table(...)` associations | Yes | New construct type, needs its own extractor function (not a `class` block) |
| Composite primary key | Yes | Any table/class with >1 `primary_key=True` column |
| Enum member values | Yes, if present | Zero real-world instances found this session — low priority |
| Relationship **kind** (one-to-many / many-to-one / many-to-many / one-to-one) | Yes, but **not from one file alone** | SQLAlchemy doesn't declare "this is one-to-many" directly — it must be *derived* by matching `back_populates` pairs across BOTH sides of a relationship and checking for `secondary=` (→ many-to-many) or `uselist=False` (→ one-to-one). This requires a **cross-entity** pass over the whole parsed model set, not a per-file extraction — a genuine step up in complexity from every extraction ADR-001/002 do today (both are strictly per-file/local). |

## 4. Proposed `EntityDefinition` additions

```python
@dataclass
class RelationshipDefinition:
    attr_name: str                  # e.g. "tags", "author"
    target_class: str               # e.g. "Tag", "User"
    back_populates: str | None
    secondary: str | None           # association-table name, if many-to-many
    kind: str | None = None         # derived in a separate cross-entity pass;
                                     # None until that pass runs

@dataclass
class AssociationTable:
    table_name: str
    source_path: str
    columns: list[FieldDefinition]  # composite PK is implicit: all columns
                                     # with is_primary_key=True

@dataclass
class EntityDefinition:
    # ...existing fields unchanged...
    relationships: list[RelationshipDefinition] = field(default_factory=list)
```

New top-level functions (siblings to, not replacements for, the existing
ones):
- `extract_relationships(model_content) -> list[RelationshipDefinition]`
  — per-file, mirrors `extract_entity_definition`'s scope.
- `extract_association_table(content, source_path) -> AssociationTable | None`
  — the new construct type; returns `None` for anything that isn't a bare
  `Table(...)` call.
- `derive_relationship_kinds(entities: list[EntityDefinition]) -> None`
  — the one genuinely new architectural piece: a cross-entity pass that
  matches `back_populates` pairs and fills in each `RelationshipDefinition.kind`.
  Kept as an explicit, separate, optional step — per-file extraction stays
  exactly as local/simple as ADR-001/002's existing functions; only this
  one function needs the full entity collection.

## 5. Downstream benefits

- **Schema generation (ADR-001)**: relationship/m2m fields are the one
  class of drift ADR-001's own "What this does not change" section
  explicitly flagged as out of scope (blog_cms's invented `tag_ids` field,
  Experiment 017). Feeding relationship data into the binding field
  manifest would close exactly that documented gap.
- **Deterministic seeding (ADR-002)**: today's `deterministic_seed_generator.py`
  cannot see relationships at all, so it can't seed an association table
  alongside its two linked entities. Extending `entity_metadata.py` would
  let ADR-002 treat an association table as a natural extension of its
  existing FK-target eligibility rule (it's arguably an even cleaner case
  than a regular lookup table, since both its columns are FKs by
  definition) — a candidate **follow-up integration task**, not part of
  this extension itself.
- **CRUD generation**: a route handler building `Post(**data, tags=[...])`
  needs to know a field is relationship-backed (not a plain column) to
  wire it correctly. Currently guessed per-attempt; a binding manifest
  extension would remove that guess the same way ADR-001 removed it for
  plain columns.
- **Validation**: `ContractConformanceValidator._check_relationship_targets_exist()`
  **already exists and is already wired into the live pipeline** (Stage
  2a2, same as `_check_api_calls_reference_endpoints` found in the ADR-003
  investigation) — but is permanently inert today because
  `ContractEntity.relationships` is never populated by the adapter. This is
  a **third** dormant validator this project has now found sharing the
  exact same shape ("the check already exists, the data feeding it
  doesn't") — extending `entity_metadata.py` and wiring its output into
  the adapter would activate it for free, the same pattern that closed out
  part of the ADR-003 investigation.
- **Browser testing**: indirect only — no direct integration point;
  benefits flow through more-correct CRUD generation, not a separate
  browser-stage hook.

## 6. Estimates

- **Engineering effort**: **M**, comparable to ADR-002's actual scope,
  possibly the upper end of M — the per-file extraction pieces
  (relationship/association-table parsing) are ADR-001/002-shaped
  (small, local, regex-or-AST), but the cross-entity `kind`-derivation
  pass is a genuinely new architectural shape for this codebase (every
  extraction so far, including ADR-001 and ADR-002, is strictly per-file).
- **Expected reduction in variance / repair loops — important caveat**:
  the case for this extension is well-evidenced from `patterns.json`'s
  full historical corpus (`SQLAlchemyError` 11 + `NoReferencedTableError`
  6 + `RelationshipModelNotImported` 3 = 20 occurrences, spanning many
  different app ideas). **But this session's own fixed 3-app canary
  (todo/blog_cms/crm) shows zero `relationship()`/`secondary=` usage in
  any generation attempt this entire session** — confirmed by grepping
  every currently-generated copy of those three apps. This means: the
  standard validation methodology this project has used for every prior
  experiment (run the fixed 3-app canary, compare scores) **cannot
  observe this fix's benefit at all** unless one of those three apps
  happens to generate a relationship-bearing model on a given attempt —
  the same "coin-flip gated" problem ADR-002 and the RouterExportMismatch
  fix both hit repeatedly this session, now identified *in advance* rather
  than discovered after a wasted canary run.
- **Expected benchmark impact on the fixed 3-app canary specifically**:
  low-to-unmeasurable, precisely because of the above. The real evidence
  for this fix's value lives in the wider historical corpus, not in what
  the standard canary would show.

## 7. Risks

- **Cross-entity `kind` derivation** is the one piece with real edge-case
  exposure: asymmetric `back_populates` (declared on one side, missing or
  mismatched on the other), a `secondary=` string referencing a table that
  was never actually defined (this is *literally* what
  `NoReferencedTableError`/`RelationshipModelNotImported` already are) —
  the derivation pass needs to degrade gracefully (leave `kind=None`, not
  crash or guess) rather than assume every relationship is cleanly paired.
- **Validation methodology gap**: per Estimate above, this cannot be
  validated the way every prior fix this session was validated (blind
  fixed-canary run). The phased plan below addresses this directly rather
  than deferring it.
- **Regex→AST migration** (recommended below) touches parsing logic
  ADR-001 and ADR-002 already depend on being correct — needs regression
  testing against their existing test suites, not just new tests for the
  new functionality.
- **Enum extraction** has near-zero demonstrated real-world payoff this
  session — a risk of investing in a feature the actual generation
  pipeline doesn't currently produce, "gold-plating" relative to observed
  behavior. Recommend deferring it to a stretch phase, not the core scope.

## 8. Phased implementation plan (for if/when approved)

- **Phase A** — migrate `entity_metadata.py`'s column parsing from regex
  to `ast`-based parsing. Foundational: fixes the pre-existing multi-line-
  `Column(...)` gap as a side effect, and gives every later phase a more
  robust base than extending the current regex approach would. Validated
  against ADR-001/002's *existing* test suites first (regression, zero
  behavior change), before any new functionality.
- **Phase B** — `extract_relationships()`: per-file `relationship()`/
  `back_populates`/`secondary` parsing, added to `EntityDefinition`. No
  cross-entity logic yet — just captures what's declared.
  **Validate via targeted, ADR-002-style fixture construction** (real
  `relationship()`-bearing model files, not a blind canary run, per the
  Estimate above), not the standard 3-app canary.
- **Phase C** — `extract_association_table()`: the new bare-`Table(...)`
  construct type, including composite-PK recognition.
- **Phase D** — `derive_relationship_kinds()`: the cross-entity pass.
  Isolated on purpose — every earlier phase stays as local/simple as
  ADR-001/002's existing functions; only this phase touches multiple
  entities at once.
- **Phase E (stretch, low priority per Section 2's negative evidence)** —
  Enum member-value extraction. Defer unless real generation output
  starts using `Enum(...)` columns.
- **Follow-up (separate task, not part of this extension)**: wire the new
  data into (a) ADR-001's schema-binding manifest, (b) ADR-002's seeder
  eligibility rule for association tables, (c) the adapter, to activate
  `_check_relationship_targets_exist()`. Each is its own small, reversible
  commit, matching this project's established discipline — don't bundle
  them into the extraction work itself.

---

## Verdict: **APPROVE FOR IMPLEMENTATION**

The philosophy fits cleanly (extends ADR-001's existing deterministic
extractor, doesn't introduce new architecture), the underlying constructs
are confirmed real and deterministically parseable against actual
generated code (not assumed), and the benefit case is well-evidenced from
the wider historical corpus (20 occurrences across many different app
generations) plus activates a third already-built-but-inert validator —
the same high-value "reuse what already exists" pattern that made ADR-001
and ADR-002 worth doing.

**The one thing that must be explicit going in** (this is the "high
frequency ≠ high architectural ROI" lesson from the endpoint
investigation, applied correctly this time rather than discovered after
the fact): this session's own fixed 3-app canary will not exercise the new
code at all, since none of todo/blog_cms/crm's current generation attempts
use relationships. Validation for Phases A-D must use targeted,
fixture-based verification (the technique already proven in the ADR-002
targeted verification), not blind canary runs — the standard canary only
becomes a meaningful signal for this work if a future canary app happens
to generate a relationship-bearing model, or if the fixed 3-app set is
ever revisited to include one that does.
