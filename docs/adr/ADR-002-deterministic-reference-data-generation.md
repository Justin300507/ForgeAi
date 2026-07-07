# ADR-002: Deterministic Reference-Data Generation

## Status

Accepted — 2026-07-07. Implemented in commits `a609753..1f3e893` (Tasks
1-5), canary evidence in `m2_canary_adr002_run.log` /
`m2_canary_adr002_confirm_run.log`, targeted verification logged in
`experiments.md` Experiment 020.

## Context (Background / Problem)

`v6_orchestrator.py`'s repair loop has a special case for
`app/routes/seed_routes.py`: if the LLM omits the file entirely, the
orchestrator does **not** call the LLM to regenerate it — a deliberate,
pre-existing decision, because the LLM has no project context in that
repair branch and had previously invented wrong-domain data (e.g. gym or
hospital records) for unrelated apps. Instead it wrote a static, hardcoded
stub:

```python
@seed_router.post('/seed')
def seed_data(db: Session = Depends(get_db)):
    return {'seeded': True, 'message': 'Demo data ready'}
```

This stub inserts nothing. Any entity with a required foreign key to a
lookup/reference table (the clearest case: todo's `Task.priority_id` →
`priorities`) then permanently fails `POST /tasks` with a 400 — the
generated route code is correct to reject a reference to a row that will
never exist. This exact failure recurred in every todo canary run across
Experiments 013, 015-019.

Experiment 019 (seed-before-CRUD journey ordering) fixed a **different**
bug in the same failure chain — the journey runner never called
`POST /seed` before attempting Create — but its own confirming canary
run surfaced this stub as the residual blocker: seeding was correctly
timed, but there was nothing to seed. Root cause was fully isolated to
one specific, narrow gap: **the zero-insert fallback stub itself**, gated
by whether the LLM happened to produce a real seed script that generation
attempt — a coin flip, not a fix.

## Decision

Replace the static stub with a deterministic generator that reuses
ADR-001's `entity_metadata.py` extractor to derive real, insertable
lookup-table rows from the already-generated SQLAlchemy models — with
**zero LLM calls, zero prompt generation, zero AI reasoning** anywhere in
the new code path.

### Core philosophy (extends ADR-001)

> Whenever multiple LLM generations would otherwise need to independently
> agree on the same information, replace that agreement with one
> deterministic artifact generated once and reused everywhere.

```
ADR-001:  Model -> Entity Metadata -> Schema
ADR-002:  Model -> Entity Metadata -> Deterministic Seeder
```

### Architecture

New module, `backend/app/services/deterministic_seed_generator.py`, built
entirely on the existing, unmodified `entity_metadata.py` — no parsing
logic duplicated anywhere (a real Task-2 review finding: an early draft
reimplemented `entity_metadata.py`'s `_singular()` helper byte-for-byte;
caught in review and removed, fixed at the correct layer instead).

- **`discover_models(project_path)`** — parses every `app/models/*.py`
  file via the existing `extract_entity_definition()`. The `users`/`user`
  table is excluded unconditionally by table-name — a deliberate, named
  exception to "no keyword matching" (confirmed by the human during
  review): it excludes the one fixed, architecturally-known auth table
  every generated app has by construction (created via register/login,
  never via seeding), not a guess about business-entity semantics.
- **`find_lookup_entities(entities)`** — two-stage, fully structural
  selection:
  1. *Candidacy*: any entity that is the FK target of some column
     (including a self-referential column).
  2. *Eligibility (transitive closure)*: a candidate is eligible only if
     every required (`nullable=False`, no default) FK it declares also
     points to another currently-eligible entity. Computed as a fixed
     point — repeatedly remove candidates with a required FK pointing
     outside the shrinking eligible set until a pass removes nothing. A
     required *self*-referential FK is excluded unconditionally, before
     the fixed-point loop, since no prior row can ever satisfy it.
     Every exclusion is logged with its reason
     (`"excluded <table>: required FK -> <target> outside the
     deterministic lookup graph"`) and generation continues with the
     reduced set — one exclusion never aborts the whole file.
- **`topological_order(entities)`** — Kahn's algorithm over the FK
  subgraph restricted to the eligible set; returns `None` (not a raise,
  not a partial order) if a cycle prevents full ordering.
- **`generate_field_value(field, class_name, loop_var="i")`** — purely
  type-driven source-code *expressions* (String/Text/Enum → a labeled
  fallback string; Integer/Numeric/Float → `i + 1`; Boolean → alternating;
  Date/DateTime → an offset from `utcnow()`; UUID → `uuid.uuid4()`),
  referencing the runtime loop variable rather than precomputed literals,
  since the actual starting row count isn't known until the generated
  code executes. Zero semantic guessing — no keyword-based value banks
  for anything resembling "priority" or "status".
- **`render_seed_routes(entities_in_order)`** — emits the complete
  `seed_routes.py` source: a version header
  (`# Generated by ForgeAI Deterministic Seeder`, `# ADR-002`,
  `# Generator Version: 1`, `# Timestamp: <ISO8601>`), one count-gated
  `_seed_<table>(db)` helper per lookup entity, and the `POST /seed`
  handler calling each helper in dependency order and returning a
  structured `summary` dict.
- **`generate(project_path)`** — the single public entry point and single
  fallback boundary. Wraps the entire discover → eligibility → order →
  render sequence in one `try/except`; returns `(source, telemetry)` on
  success or `(None, telemetry)` for every failure condition (no models,
  no lookup entities, FK cycle, any exception). **Never raises.**

### Deterministic principles enforced throughout

- No entity-name/keyword special-casing (the one exception, `users`, is
  named and justified above).
- No relationship/secondary-table parsing (out of scope — a future
  extension, see below).
- No runtime lookups against *out-of-graph* entities. A required external
  FK (e.g. a lookup table that mandates a real `owner_id`) causes that
  entity to be excluded from seeding entirely — a deliberate simplification
  over attempting a live, conditional runtime query, chosen specifically
  because "maybe skip the row" branching depends on live database state,
  the ORM session, and another execution path, which is exactly the kind
  of complexity this design exists to avoid. If an entity fundamentally
  requires `User`/`Organization`/`Tenant` to exist, it is application data,
  not standalone reference data, and does not belong in this fallback.
- Internal FK chains (a lookup entity required-FKing *another* lookup
  entity) **are** resolved — via a runtime pool query against the
  already-seeded sibling (`db.query(ParentClass).all()`, indexed by
  `i % len(pool)`), since the parent is guaranteed already seeded by
  topological order within the same request. This is categorically
  different from the out-of-graph case: the query targets our own
  just-inserted deterministic data, not uncertain external state.

### Count-based idempotency

```python
TARGET_ROWS = 3

def _seed_<table>(db: Session) -> dict:
    existing = db.query(<Model>).count()
    if existing >= TARGET_ROWS:
        return {"inserted": 0, "skipped": TARGET_ROWS, "already_existed": existing}
    inserted = 0
    for i in range(existing, TARGET_ROWS):
        try:
            db.add(<Model>(...))
            db.commit()
            inserted += 1
        except IntegrityError:
            db.rollback()
    return {"inserted": inserted, "skipped": TARGET_ROWS - existing - inserted,
            "already_existed": existing}
```

Two alternatives were considered and rejected:

- **Field-guess** (assume the first non-FK required column is a natural
  key): fragile — which field "identifies" a row varies arbitrarily
  across schemas, and there's no reliable way to pick it without keyword
  guessing.
- **`UNIQUE`-constraint-based check**: correct only when such a
  constraint exists. Most LLM-generated lookup tables don't declare one,
  which means `IntegrityError` would never fire and rows would silently
  duplicate on every repeat `POST /seed` — a silent violation of
  idempotency in the common case.

`db.query(Model).count() >= TARGET_ROWS` needs nothing beyond what
`entity_metadata` already parses (`table_name`), is idempotent by
construction (a second call always sees `count >= 3` and returns
immediately), and required zero expansion of `entity_metadata.py`. The
per-row `try/except IntegrityError/rollback` remains as defense-in-depth
only — correctness never depends on it firing.

### Telemetry

At codegen time (`v6_orchestrator.py`, when this fallback fires):
lookup-entity count, exclusion log, fallback-used flag and reason,
generation time in milliseconds — printed to the same canary logs every
other experiment in this project already relies on. At runtime, the
generated `POST /seed` response includes a structured per-entity
`{inserted, skipped, already_existed}` summary; `user_journey_runner.py`'s
pre-existing best-effort `/seed` call now captures this into a new
`JourneyResult.seed_summary` field, surfaced in `backend_runner.py`'s
`journey_data` and console output — no new capture mechanism, reusing the
call that already existed for Experiment 019.

## Validation Evidence

Five independent lines of evidence, not one benchmark:

1. **Unit/execution tests (35 passing, zero failures)**: fixture-driven
   tests against real parsed model strings for every structural rule
   (FK-target candidacy, transitive eligibility including a genuine
   two-hop cascade, self-reference exclusion, cycle detection, nullable-
   vs-required external FK handling), plus execution-based tests that
   `exec` the rendered source against a real in-memory SQLite database to
   prove count-based idempotency and partial-data top-up actually work at
   runtime, not just structurally.
2. **Local validation across all 5 implementation tasks**: each task
   reviewed against the frozen spec by an independent reviewer subagent
   before merge; two rounds caught and fixed real issues (a duplicated
   `entity_metadata.py` helper; a telemetry field silently frozen at
   `0.0` on 3 of 4 return paths) before they reached the live pipeline.
3. **Live canary telemetry** (`m2_canary_adr002_run.log`): the fallback
   fired twice across a 3-app canary. **todo**: seeded 3 real `Priority`
   rows, confirmed via runtime telemetry, reproduced identically across
   5 journey runs. **blog_cms**: correctly *excluded* `Post` (required FK
   to `users`) while correctly seeding the one genuine lookup entity —
   the transitive-eligibility rule enforcing its "no business data" line
   exactly as designed, observed live, not just in a unit test.
4. **Targeted deterministic verification (strongest evidence)**: a
   second canary run happened to trigger the fallback zero times (the
   LLM produced real seed scripts for all three apps that attempt) —
   rather than spend a third blind canary hoping to catch the coin-flip
   again, the exact code path was forced on demand. A real generated
   project (`todo_list_app`) was copied to a scratch directory, one
   known *unrelated* bug was neutralized (a malformed constructor in
   `task_routes.py`, nothing to do with seeding), `seed_routes.py` was
   deleted, `generate()` was called directly, and the real FastAPI app
   was started and driven with real HTTP requests: `POST /seed` → 3 rows
   inserted; `POST /tasks` referencing the seeded `priority_id` → `201
   Created` — the exact failure this ADR exists to fix, eliminated,
   observed directly rather than inferred from a confounded aggregate
   score.
5. **Negative evidence**: in the run where the fallback never fired,
   `blog_cms`'s score continued declining anyway — confirming that
   regression traces to a pre-existing, unrelated endpoint-naming drift
   defect, not to this feature.

Full detail and raw log excerpts: `experiments.md`, Experiment 020.

## Consequences

**Positive**: converts a previously LLM-coin-flip-gated failure mode into
a deterministic, zero-cost, always-correct fallback. Adds no new LLM
calls or spend (confirmed — `generation_time_ms` in the low single-digit
milliseconds, consistent with pure local computation). Reuses ADR-001's
extractor rather than duplicating parsing logic, continuing the
"generate once, reuse everywhere" pattern as an explicit, repeatable
architecture principle rather than a one-off fix. Adds telemetry
(codegen-time and runtime) that made every claim in this document directly
checkable rather than inferred.

**Negative / limitations**:
- Only seeds pure reference/lookup entities identified by the FK-target +
  transitive-eligibility rule. Any entity requiring a real external
  dependency (`User`/`Organization`/`Tenant`, directly or transitively) is
  excluded from seeding entirely, not worked around.
- Does not seed relationship/secondary-table (`relationship()`,
  many-to-many) entities — the extractor only reads `Column(...)`
  definitions, matching ADR-001's existing scope boundary.
- Row values are generic and mechanical (`"Priority 1"`, `"Priority 2"`,
  ...), not domain-realistic — this fallback was never positioned to
  produce realistic demo data, only to unblock FK-validated CRUD testing
  when the LLM-authored path (which does aim for realistic data) fails to
  produce a file at all.
- Exactly 3 rows per entity, fixed — not configurable per-app.

## Non-Goals (explicitly out of scope this cycle)

- Redesigning or touching the primary LLM-authored `seed_routes.py`
  prompt path (`shared_contract.py`) — untouched; this ADR governs the
  fallback branch only.
- Relationship/secondary-table seeding.
- Composite lookup entities (multi-column natural keys) — moot under
  count-based idempotency, which needs nothing beyond `table_name`.
- Enum-type member-value extraction (an `Enum` column is treated as a
  plain string type today).
- Adding `unique=True` parsing to `entity_metadata.py` — not needed;
  count-based idempotency requires nothing beyond what it already parses.

## Rollback Strategy

This fallback path can be disabled by reverting the single wiring commit
in `v6_orchestrator.py` (the call site that invokes
`deterministic_seed_generator.generate()`), restoring the static-stub-only
behavior with zero other code depending on the new module. No feature
flag was added — the design's own fallback boundary (`generate()` never
raises, degrades to the static stub on any failure) already provides the
safety net a flag would otherwise exist for.

## Future Extensions (list only — do not implement without a separate experiment)

- Relationship/secondary-table (`relationship()`, many-to-many) seeding —
  would require extending `entity_metadata.py`'s extractor, a natural next
  step already flagged in ADR-001.
- Enum member-value extraction, so `Enum` columns get realistic member
  values instead of the generic string fallback.
- Composite lookup entities.
- Advanced deterministic defaults beyond the current basic type-driven
  set (e.g. format-aware string generation for fields recognizable as
  emails/phone numbers by *type*, not by name-keyword-matching).

## What This Does Not Change

This ADR does not authorize expanding the deterministic seeder beyond
reference/lookup-table seeding, adding relationship parsing, or starting
work on any of the Future Extensions above without its own design and
validating benchmark. Per the project's roadmap (V16 Phase 3: reference
data reliability, now substantially addressed by this ADR), the next
priorities — Phase 4 (frontend import/build reliability) and Phase 5
(endpoint/route naming consistency, the specific defect class this
canary's blog_cms regression traced to) — are evaluated independently and
prioritized by fresh telemetry, not folded into this one retroactively.
