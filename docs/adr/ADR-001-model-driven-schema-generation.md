# ADR-001: Model-Driven Schema Generation

## Status

Accepted — 2026-07-06. Implemented in commits 97e17d2, 0b799a0, 4887584.

## Context

Backend generation runs as independent, parallel waves per file
(`app/services/parallel_backend_service.py`): Wave 2 generates every
SQLAlchemy model (one LLM call per table), Wave 3 generates every Pydantic
schema (one LLM call per resource). Both waves are given the same
architect-level description of an entity, but as two separate LLM calls
with no enforced agreement between them.

This repeatedly caused **model/schema field-name drift**: the model would
declare one set of column names while the schema independently invented
another. The clearest instance, traced across Experiments 010–016: the
`simple_crm` `Contact` model declared a single `name` column
(`nullable=False`), while `ContactCreate` declared `first_name`/`last_name`
instead. Since the route layer builds the ORM object from
`{k: v for k, v in schema_data.items() if k in Model.__table__.columns}`,
the mismatched field was silently dropped and the database rejected the
`NULL` at `INSERT` time — collapsing every downstream CRUD step
(Edit/Delete/Verify) with "no entity_id captured".

Per the VNext architecture report, this class of cross-file/cross-stage
disagreement (model↔schema, schema↔route, endpoint↔frontend) accounts for
an estimated 58% of all historical failure instances — the single largest
recurring disease in the system. The report's proposed cure is a full
AppContract typed IR: architect emits a machine-validated contract, every
generator consumes it, a conformance validator checks the result. That is
an L-effort (1–2 week), architecture-wide change touching the architect,
every generator, and a new validation stage.

Investigation (Experiment 017) found the mechanism to prevent the
model↔schema slice of this disease **already existed** in the code:
`_gen_schema` already accepted a `model_content` parameter, and the schema
prompt already labeled it "CORRESPONDING MODEL (for field reference)". It
was defeated by two bugs:

1. Wave 3's model-content lookup used a naive `resource.py` /
   `resource[:-1]+".py"` filename guess that doesn't handle every
   singular/plural naming convention the architect's endpoint spec can
   produce.
2. Wave 2.5's singular-shim step (`created shim app/models/{singular}.py`
   — a bare two-line re-export, e.g. `from app.models.contacts import
   Contact`, with zero column data) registers that shim into the *same*
   `model_contents` lookup dict the real model lives in. Wave 3's naive
   lookup checked the singular path first, so it could silently receive a
   contentless shim instead of the real model — leaving the schema LLM
   call with no real field information and no choice but to guess.

## Decision

All Pydantic schema generation derives from the actual generated
SQLAlchemy model, not independently from the architect description alone.

Concretely:

- A new, reusable module, `app/services/entity_metadata.py`, provides
  `EntityDefinition`/`FieldDefinition` and:
  - `extract_entity_definition(model_content)` — parses a model file's
    real `Column(...)` definitions (type, nullable, default, primary/
    foreign key, FK target). A bare re-export shim has no `Column(...)`
    lines and correctly parses to `None` — it can never again be mistaken
    for a real model.
  - `find_model_for_resource(model_contents, resource)` — resolves the
    real model by parsed `table_name`/`class_name`, never by filename
    guessing, so singular/plural naming mismatches resolve correctly
    regardless of which file happens to hold the real columns.
  - `render_field_manifest(entity)` — renders an explicit, binding field
    list for prompt injection (required vs. optional, PK/FK annotations).
- Wave 3 (`parallel_backend_service.py`) resolves the real model via this
  extractor and injects a "BINDING FIELD CONTRACT" block into the schema
  prompt (`build_schema_prompt`, `app/prompts/parallel_backend_prompt.py`)
  in place of the old advisory "for reference" framing. Required fields
  must appear in the Create schema under their exact name; primary/foreign
  keys must not appear at all.
- Gated behind `FORGE_MODEL_DRIVEN_SCHEMA` (env var). **Default: enabled.**
  Set to `0` to roll back to the old filename-guess lookup instantly,
  without a code change or redeploy, if a regression is ever traced here.

This is deliberately narrower than AppContract: it only synchronizes
model↔schema field names via SQLAlchemy `Column(...)` parsing. It does
not cover relationships/secondary tables (many-to-many fields, e.g.
blog_cms's `tags`), endpoints, routers, or imports.

## Result

Two canaries (Experiments 017, 018; `todo`/`blog_cms`/`crm`, Gemini):

- **Direct, file-level confirmation** the drift class is eliminated:
  `ContactCreate` now declares `name`/`status` under the model's exact
  column names, both runs, across independent generation attempts.
- **The reactive patch that used to paper over this** (`[field_patcher]
  Added missing schema field(s) ['name']`, first seen in Experiment 012)
  did not fire for the target entity in either confirming run — the field
  was correct from the first generation attempt, not patched in
  afterward.
- **Experiment 018** (clean confirming run, no code changes before it):
  `CANARY PASSED`. Both apps that had been blocked by this exact drift in
  every prior run this cycle achieved a full CRUD pass for the first time
  all session:

  | App | Forge Score | Build | Runtime | CRUD Journey |
  |---|---|---|---|---|
  | crm | 91.4 (A) | ✅ | ✅ | 11/11 passed (Create 201, Edit 200, Delete 204) |
  | blog_cms | 90.3 (A) | ✅ | ✅ | 11/11 passed |
  | todo | 73.9 (C) | ✅ | ⚠️ | unrelated pre-existing failure (unseeded Priority lookup table) |

- Zero regressions were attributable to the feature across both canaries;
  every remaining failure traced to an independent, already-catalogued
  cause outside this mechanism's scope (a `ConfigAttributeError` variant,
  an invented `tag_ids` relationship field, an unseeded lookup table).

## Consequences

**Positive**: eliminates a specific, high-frequency, well-evidenced
failure class with a small, isolated, reusable change — no new IR, no new
validation stage, no architecture-wide rewrite. The extractor is designed
for reuse beyond schema generation (routes, validators, docs, and as a
building block for a future, narrower AppContract).

**Negative / limitations**:
- Does not cover relationships or secondary-table (many-to-many) fields —
  those are read from `relationship(...)` calls, which this extractor does
  not parse. Explicitly deferred as a separate future experiment, not
  folded into this one.
- Does not cover schema↔route or endpoint↔frontend drift — those remain
  open failure classes (candidates: the "AppContract for routes/endpoints"
  scope, now smaller since the model↔schema slice is solved).
- Wave 3 now has a (previously-latent, already-structurally-present)
  dependency on Wave 2's actual output for entities it resolves; no new
  latency was observed since Wave 3 already ran after Wave 2 completed.

## What this does not change

This ADR does not authorize expanding the extractor, adding relationship
parsing, or starting AppContract work. Per the project's roadmap, the
next priorities are evaluated independently and prioritized by telemetry:
remaining JourneyCRUDFailure edge cases, relationship extraction (a
separate future experiment), frontend import reliability, and — only
after those — a narrower AppContract scoped to routes/endpoints (a
smaller scope now that this ADR has already resolved the model↔schema
slice of that problem).
