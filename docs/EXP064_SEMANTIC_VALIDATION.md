# Experiment 064 — Semantic Write Validation

2026-07-12. Extends `write_fix()` with one narrowly-scoped semantic
consistency check, closing the exact gap Exp063 identified. Offline
only — no generation, no LLM calls, no prompt changes, no new repair
heuristics.

## Root cause (from Exp063, restated for this doc's completeness)

`write_fix()` (`app/services/fix_writer_service.py`) validates that an
LLM-returned "entire corrected file" **parses** (`_is_safe_to_write`,
`ast.parse`), but never checked whether the file is **internally
self-consistent**. Exp063 proved, by direct comparison against
ForgeAI's own provably-correct static auth template, that a repair pass
had rewritten `auth_routes.py`'s `signup()` handler to read
`req.username` while the same file's `SignupRequest(BaseModel)` class
never declared a `username` field — perfectly valid Python, guaranteed
`AttributeError` at runtime. This exact corruption was found,
byte-identical, in two independent live app generations (`todo`,
`blog_cms`).

## Algorithm

Implemented in `app/services/fix_writer_service.py`, called from
`write_fix()` immediately after the existing syntax guard:

```
_check_request_field_consistency(path, content) -> (bool, str | None)
```

1. **Find request model classes** (`_collect_basemodel_classes`): walk
   every `ast.ClassDef` in the file; a class is "Pydantic" if it
   inherits `BaseModel` directly, or (transitively, within the same
   file only) inherits another locally-defined Pydantic class — a
   fixed-point resolution over local inheritance, so
   `class SignupRequest(BaseRequest)` correctly picks up `BaseRequest`'s
   fields too when `BaseRequest` is itself defined in the same file.
2. **Collect declared fields**: for each Pydantic class, every
   `AnnAssign`/`Assign` target at class-body level (typed and untyped
   field declarations, e.g. `email: str` and `model_config = {...}`),
   plus every method/property name (so a `@property` or validator
   method is never flagged as a "missing field").
3. **Find route handlers**: every `FunctionDef`/`AsyncFunctionDef` in
   the file (via `ast.walk`, so nested handlers are found too).
4. **Track request parameter types**: for each handler's parameters,
   resolve the annotation to a known Pydantic class name
   (`_annotation_class_name`) — supports a bare `Name` (the confirmed
   failure shape), and the common `Optional[Name]` / `Name | None`
   variants. Anything else (`List[X]`, a name not in the discovered set,
   …) resolves to "not applicable," not an error.
5. **Collect attribute accesses** (`_collect_param_attribute_accesses`):
   for each typed parameter, walk the handler body collecting every
   `param.attr` access — including inside f-strings, comprehensions, and
   nested `if`/`for`/`with`/`try` blocks (none of those open a new scope
   in Python). Correctly **stops descending into a nested function or
   lambda that re-binds the same parameter name** (shadowing) — that
   nested scope refers to a different object and gets its own
   independent check when the outer scan reaches it directly.
6. **Verify**: `attribute ∈ declared fields` (or a reserved Pydantic
   attribute like `model_dump`, or a dunder). If not: return
   `(False, reason)` with the exact file, line, function, class, and
   declared-field list. `write_fix()` prints the reason and returns
   `False` without writing — the same shape as the existing syntax
   guard, so callers (already tolerant of a `False`/no-write outcome
   per the established repair-loop contract) continue unaffected.

## Files changed

- `backend/app/services/fix_writer_service.py` — added
  `_PYDANTIC_RESERVED_ATTRS`, `_collect_basemodel_classes`,
  `_annotation_class_name`, `_shadows_name`,
  `_collect_param_attribute_accesses`,
  `_check_request_field_consistency`; `write_fix()` now calls the new
  check right after `_is_safe_to_write()`, before the file is written.
- `backend/tests/reliability/test_semantic_write_validation.py` (new) —
  24 tests.

No other file was touched. No prompt file was touched. No existing
function's behavior changed except `write_fix()` gaining one additional
rejection path.

## New validator behavior

`write_fix()` now rejects a write (prints
`"=== REFUSING SEMANTICALLY INCONSISTENT WRITE: {path} ==="` plus the
exact reason, returns `False`, does not touch disk) whenever a locally
Pydantic-typed route-handler parameter has an attribute access the
class doesn't declare. It is a **pure additional gate** — every existing
accept path is unchanged; the only new observable behavior is this one
new rejection path, and it only fires for the specific shape described
above.

## Replay results (the task's own explicit verification requirement)

Ran the check directly against the real, on-disk files from Exp062's
live runs (not synthetic reproductions):

| File | Result | Evidence |
|---|---|---|
| `generated_projects/todo_list_app/app/routes/auth_routes.py` (confirmed corrupted) | **Rejected** | `app/routes/auth_routes.py:96: 'req.username' accessed in signup(), but class SignupRequest (defined in this same file) has no field 'username' -- declared fields: ['display_name', 'email', 'model_config', 'password']` |
| `generated_projects/forge_blog_cms/app/routes/auth_routes.py` (confirmed corrupted, byte-identical bug) | **Rejected** | Identical reason, same line, same file shape — confirms the check generalizes correctly across independent app generations, not overfit to one file. |
| `generated_projects/simple_crm/app/routes/auth_routes.py` (pristine, matches the static template exactly) | **Passed** | `consistent=True` |
| `generated_projects/inventory_manager/` (all 31 real `.py` files) | **Passed, all 31** | Zero flags — confirms "inventory unaffected" exactly as the task required. |

**Also swept every real `.py` file in all 4 affected projects** (115
files total: 27 in `todo_list_app`, 30 in `forge_blog_cms`, 27 in
`simple_crm`, 31 in `inventory_manager`) — **exactly and only the 2
already-confirmed corrupted files were flagged.** Zero false positives,
zero false negatives relative to what Exp063 established, across real
generated code from 4 independently-generated apps.

`write_fix()` end-to-end (not just the standalone check function):
confirmed a call with the corrupted content returns `False` and the
file is genuinely never created on disk; confirmed a call with the
correct content returns `True` and the file is written; confirmed the
pre-existing syntax guard still fires first and independently for
genuinely broken Python (composition of the two guards verified, not
assumed).

## Tests

24 tests in `tests/reliability/test_semantic_write_validation.py`,
covering every category the task specified:
- **Correct request** / **incorrect request** (synthetic, minimal)
- **Multiple request models** in one file (only the actual mismatch
  flagged; a second, fully-correct model in the same file doesn't
  trigger a false positive)
- **Nested handlers**: both shapes — a nested closure that refers to
  the SAME outer parameter (must still be checked, and is) and a nested
  function that **shadows** the parameter name with its own,
  differently-typed parameter (must NOT be checked against the outer
  class, and isn't)
- **Existing syntax failures**: a file that doesn't parse returns
  `(True, None)` from this check specifically — the pre-existing syntax
  guard owns that rejection, this check doesn't duplicate or interfere
  with it
- **False-positive protection**, four distinct cases: a real
  `@property`, Pydantic's own reserved attributes (`model_dump`,
  `model_config`, …), a non-Pydantic class (out of scope, correctly
  ignored), and `Optional[X]`/`X | None` annotations (correctly resolved,
  not silently skipped)
- **Local inheritance** (a class inheriting a locally-defined Pydantic
  base correctly inherits its fields; a mismatch against an inherited
  field is still caught)
- **Live replay** against all 4 real generated projects (above)
- **`write_fix()` end-to-end** composition with the pre-existing syntax
  guard

Full existing suite (50 test files, up from 49 — this experiment's own
new file) plus `tests/adr002/test_orchestrator_wiring.py` re-run and
confirmed passing. One transient failure
(`test_role_aware_journey.py::test_elevates_and_passes_when_role_vocabulary_is_discoverable`)
was investigated directly — reproduced once, then confirmed to pass
cleanly on every subsequent run after clearing stale `__pycache__`
bytecode; a second full clean run showed 50/50 passing. Reported
honestly as an investigated-and-explained flake, not silently ignored.

## False-positive analysis

Zero false positives found across:
- 115 real, independently-generated Python files spanning 4 different
  apps (auth code, CRUD routes, schemas, models, seed scripts, stats
  endpoints — the full breadth of what a real generation produces).
- 24 targeted synthetic tests specifically designed to probe likely
  false-positive shapes (properties, reserved Pydantic attributes,
  non-Pydantic classes, Optional annotations, parameter shadowing,
  multi-model files).

The design choices that make this possible: (1) methods and properties
count as "declared" (a real, common pattern this check must not break);
(2) Pydantic's own reserved instance/class attributes are explicitly
allow-listed; (3) parameter shadowing in nested scopes is detected and
respected, not naively flattened; (4) anything outside the narrow shape
(non-Pydantic types, unsupported annotation forms, cross-file
inheritance) resolves to "not applicable" rather than being force-fit
into a check that would be unreliable for it.

## Limitations

- **Single-file only, by design** — a request model imported from a
  different file than the handler that uses it is not checked (would
  require cross-file resolution, explicitly out of scope per "no
  generalized semantic analyzer"). The confirmed Exp063 failure case is
  entirely single-file (`SignupRequest` and `signup()` are defined in
  the same `auth_routes.py`), so this doesn't limit the check's value
  for the confirmed gap — but a similar corruption split across two
  files would not be caught.
- **Annotation resolution is intentionally narrow** — `List[X]`,
  `Dict[str, X]`, and other container/generic shapes are not resolved
  to their inner type; a mismatched attribute access on, say, an item
  drawn from a `List[SignupRequest]` parameter would not be checked.
- **No `**kwargs`/`getattr(req, ...)`/dynamic access detection** — only
  literal `param.attr` `ast.Attribute` nodes are checked; a dynamically
  constructed attribute name (`getattr(req, field_name)`) is invisible
  to this check by construction (this mirrors how the confirmed Exp063
  bug actually manifested — a literal `.attr`, not dynamic access — so
  it's not a gap against the validated failure class, just a boundary
  of what a static AST check can ever see).
- **Does not attempt to validate the reverse direction** (a declared
  field that's never read) — that's a dead-code/unused-field question,
  a different concern entirely, correctly out of scope.

## Future extensions (not implemented, flagged only)

1. Extend to SQLAlchemy model attribute access the same way (a route
   accessing `user.some_column` where the model doesn't declare it) —
   structurally the same algorithm, a different "declared fields"
   source (`Column()`/`mapped_column()` assignments instead of Pydantic
   field declarations).
2. Resolve simple cross-file imports for the request-model class when
   the import is a plain `from app.schemas.x import Y` (bounded,
   single-hop resolution — not a general import graph walk).
3. Wire this same check into `deterministic_patcher.py`'s own
   LLM-fix-adjacent write paths (currently only `write_fix` — used by
   `v6_orchestrator.py`'s validation-loop and runtime-fix-loop repair
   paths — was in scope for this experiment; `app/repair/orchestrator.py`'s
   separate `_regenerate_module`/`_regenerate_arch` file-writing paths,
   identified in Exp061 as a distinct, uninstrumented mechanism, were
   not touched this cycle).

## Recommendation: does this belong in `write_fix` permanently?

**Yes.** Evidence for permanence, not just a one-off patch:
- It caught the *exact*, already-confirmed, twice-independently-occurring
  production failure class with zero tuning needed beyond the algorithm
  described above.
- Zero false positives across every real file available to test against
  (115 files, 4 independently-generated apps) plus 24 targeted synthetic
  probes.
- The performance cost is negligible — one additional `ast.parse` (the
  tree is walked once more; `_is_safe_to_write` already parses it
  earlier, so this is an `O(2×n)` walk, not new I/O, no new AST parse
  from a caching standpoint if a future cycle chooses to share the tree
  — not attempted here to keep this change minimal).
- It composes cleanly with the existing syntax guard (verified,
  not assumed) and requires zero changes to any caller of `write_fix`
  (the function's signature and return-value contract are unchanged).

The one caveat: this check is narrow **by design**, and should stay
that way unless a *specific, confirmed* new failure class (the same
rigor Exp063 applied here) justifies widening it — per this
experiment's own explicit rule against becoming "a generalized semantic
analyzer."
