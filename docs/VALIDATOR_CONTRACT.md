# Validator Contract (Experiment 060)

2026-07-12. Defines the canonical `Diagnostic` object every validator in
ForgeAI should produce, and the migration mechanism that gets there
without a flag-day rewrite. Direct follow-up to Experiment 059's finding
that the validator subsystem exposes 4 incompatible result shapes, one
of which already caused a confirmed production bug (see
`docs/VALIDATOR_REVIEW.md`).

## The canonical object

Reused and extended the existing `Diagnostic` dataclass
(`app/core/context.py`) rather than inventing a parallel type — it was
already the shape `verification/engine.py` builds internally for every
diagnostic that reaches the repair layer, and Exp059's own review found
no reason a second canonical type should exist alongside it.

```python
@dataclass
class Diagnostic:
    error_id:       str                    # content-derived hash, stable across re-verification
    category:       ErrorCategory           # enum: syntax/import/runtime/browser/api/dependency/
                                             #   architecture/security/env_var/contract/integration/regression
    severity:       ErrorSeverity           # enum: critical/high/medium/low/info
    source:         str                     # "static" | "runtime" | "browser" | "api"
    message:        str
    file_path:      Optional[str]  = None
    line_number:    Optional[int]  = None
    stack_trace:    Optional[str]  = None
    fix_hint:       Optional[str]  = None
    related_ids:    list[str]      = []
    metadata:       dict[str, Any] = {}
    # Added this experiment, all optional, all additive:
    validator_name: Optional[str]   = None  # e.g. "validate_orm_usage" -- which check produced this
    column:         Optional[int]   = None  # reserved; no current validator is column-precise
    code:           Optional[str]   = None  # reserved; distinct from error_id's content hash
    repairable:     Optional[bool]  = None  # None = not yet classified
    confidence:     Optional[float] = None  # reserved for probabilistic/LLM-judge validators
    duration_ms:    Optional[float] = None  # set when the producing validator times itself
```

**Fields deliberately not added**, per the task's own "do not invent
unnecessary fields" rule:
- `evidence` — the existing `metadata: dict` already serves this purpose
  generically (e.g. `validate_duplicate_class_definitions` puts its
  `locations` list there); a separate field would duplicate it.
- `column` is present but genuinely unused by any current validator —
  kept because it costs nothing (optional, defaults `None`) and the task
  explicitly asked for it, but not force-populated anywhere.

**Why extend `Diagnostic` in place rather than subclass or wrap it:**
every existing `Diagnostic(...)` construction site in the codebase
(confirmed via grep — all of them, throughout `verification/engine.py`)
uses keyword arguments, never positional. Appending new fields with
defaults after the existing ones is 100% backward compatible by
construction — verified directly (see `docs/VALIDATOR_MIGRATION.md`
§Verification).

## Why the shared `errors: list[str]` was NOT converted to `list[Diagnostic]`

This was the first design considered and rejected, with evidence, not
assumption. `validate_project()`'s `errors` list is consumed by
**~15 call sites** across `v6_orchestrator.py`, `project_service.py`,
`batch_runner.py`, and `architecture_tournament_service.py` — confirmed
by direct grep and read, not estimated. Several of these do operations
that would break or behave differently if fed `Diagnostic` objects
instead of strings:

- `v6_orchestrator.py:337`: `frozenset(validation["errors"])` — a
  standard `@dataclass` (no `frozen=True`) has `__hash__` set to `None`
  by Python's own rules, making `Diagnostic` **unhashable**. This line
  would raise `TypeError` outright.
- Multiple sites do direct string formatting / substring filtering on
  each entry (`for err in validation["errors"]: ...`), which would
  silently produce wrong output (`repr(Diagnostic(...))` instead of the
  readable message) rather than crashing — a worse failure mode.
- `validate_project()`'s own final dedup step,
  `list(dict.fromkeys(errors))`, also requires hashable elements.

Changing all ~15 consumers to handle a mixed or `Diagnostic`-only list
would be a real, wide-blast-radius refactor — exactly the kind of change
this experiment's own rules forbid ("do not refactor unrelated code").

## The actual design: an additive, parallel `diagnostics` list

`errors: list[str]` is **untouched** — confirmed byte-identical
before/after via `git stash` (see `docs/VALIDATOR_MIGRATION.md`
§Verification). `validate_project()` now ALSO builds a `diagnostics:
list[Diagnostic]`, populated only by validators explicitly migrated this
cycle, and returns it as a new, additive key:

```python
{
    "passed": bool,           # unchanged
    "errors": list[str],      # unchanged, byte-identical
    "diagnostics": list[Diagnostic],  # NEW, additive
}
```

Each migrated validator function gained one new, optional, trailing
parameter: `diagnostics: list | None = None`. Confirmed via grep that
none of these functions are called from anywhere except
`validate_project()`, so adding a trailing optional parameter cannot
break any other caller. Any code still calling a migrated validator the
old way (`validate_X(project_path, errors)`, 2 args) keeps working
identically — the new parameter simply defaults to `None` and the
validator produces only its string exactly as before.

Inside a migrated validator, the exact same message string is used for
both outputs — built once into a local variable, then appended to
`errors` and used as `Diagnostic.message`:

```python
msg = f"Router export mismatch in app/routes/{file}. Expected '{expected_router}'"
errors.append(msg)
if diagnostics is not None:
    diagnostics.append(Diagnostic(message=msg, file_path=f"app/routes/{file}", ...))
```

This makes the two outputs provably consistent (same content, always)
rather than two independently-maintained representations that could
drift apart.

## The consumption boundary: `verification/engine.py`

The one place `errors` gets converted into `Diagnostic` objects for the
repair layer (`_run_static_validators`, ~line 55) now prefers a native
`Diagnostic` when one exists (matched by exact message string), falling
back to the pre-existing regex-based construction
(`_categorise_static`/`_severity_static`/`_filepath_static`/`_hint_static`)
for anything not yet migrated:

```python
_diag_by_message = {d.message: d for d in validation.get("diagnostics", [])}
diagnostics = [
    _diag_by_message[err] if err in _diag_by_message else Diagnostic(...)  # old regex path, unchanged
    for err in validation.get("errors", [])
]
```

This is the **only** downstream consumer that needed a change. Checked
and confirmed not needing changes, per the task's own "only where
necessary" rule:
- **Observatory** (`app/memory/reliability_metrics.py`, `/observatory`
  route) never touches live `Diagnostic` objects — it only reads
  pre-serialized `generation_log.jsonl` / `canary_history.json` summary
  JSON. Confirmed via `test_observatory_compute_functions_unaffected`.
- **API** — confirmed via grep that no route in `main.py`/`app/routes/`
  directly returns `validation["errors"]` or `validation["diagnostics"]`.
- **CLI** — no CLI tool in this codebase consumes raw validator output
  directly (all reporting goes through the Forge Score / VerificationResult
  layer, unaffected).
- **Logging/metrics** — unchanged; each validator's existing `print()`
  calls (where present) are untouched, and no new logging was added to
  the 13+2 migrated validators this cycle (out of scope — see
  `docs/VALIDATOR_REVIEW.md` recommendation #2/#3 for that separate,
  not-yet-done item).

## Category/severity assignment: exact parity with the pre-existing heuristics, deliberately

Every migrated validator's `category`/`severity` was chosen to **exactly
match** what `verification/engine.py`'s existing regex-based
`_categorise_static`/`_severity_static` functions would already assign
to that exact message text — not "improved" or re-derived independently.
This was a deliberate choice, not an oversight: `category`/`severity`
feed into downstream repair-strategy selection, and changing them (even
to something more semantically correct, like using `ErrorCategory.ARCHITECTURE`
instead of the generic `ErrorCategory.CONTRACT` default for
`validate_architecture`'s messages) would be a genuine behavior change
this experiment's rules explicitly forbid. Every mapping decision is
documented inline as a code comment at its call site, citing which old
heuristic pattern it matches.

## What DID structurally improve, and why that's not a forbidden "behavior change"

`file_path` is now validator-supplied (accurate, known at the point the
validator already computed it during its own AST/file walk) instead of
regex-extracted from the message text after the fact. This is the direct
fix for the confirmed production bug this experiment exists to address.
It is not flagged as a forbidden behavior change because:
1. The task's own context explicitly cites this bug as the motivation
   for doing this work at all.
2. It doesn't change `errors`, pass/fail outcomes, or which validators
   run — only enriches what accompanies an existing error.
3. `error_id` (used for cross-attempt regression detection within one
   generation run) remains internally stable across retry attempts for
   the same persisting error post-migration — the property that
   actually matters for the fix loop — even though its specific hash
   value differs from what the pre-migration regex-derived path would
   have produced for the exact same message (since the hash formula
   incorporates `file_path`, and that's now populated instead of empty).
   No code anywhere compares `error_id` values across a code deployment
   boundary — only within a single run's own history — so this has no
   observable effect on any existing behavior.

See `docs/VALIDATOR_MIGRATION.md` for the full per-validator migration
table and verification evidence.
