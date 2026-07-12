# Experiment 087 — Root Cause Investigation of PydanticSerializationError

2026-07-12. Investigation only, $0, zero Cerebras calls — reconstruction
via real, already-on-disk generated projects proved entirely sufficient;
no live reproduction needed.

## 1. Collected occurrences

`failure_memory/patterns.json`: 6 all-time instances (`first_seen`
2026-06-29, `last_seen` 2026-07-12 — this run's Exp086 canary added the
6th), across 5 distinctly-named projects: `blog_platform`, `recipe_share`
(×2), `simple_notes_app`, `forge_blog_cms`, `todo_list_app`.
`generation_log.jsonl` (a smaller, newer telemetry file): 2 entries,
scores 65.9 and 76.9, both `succeeded=false`.

## 2. One root cause, not multiple subclasses

Checked every one of these projects still present on disk (4 of 5 —
`blog_platform`'s folder is gone/regenerated without the bug) and found
**the identical shape in every single one**:

```
recipe_share/app/routes/rating_routes.py:11:  response_model=dict
recipe_share/app/routes/recipe_routes.py:43:  response_model=dict
simple_notes_app/app/routes/note_routes.py:33: response_model=dict
simple_notes_app/app/routes/user_routes.py:13: response_model=Dict[str, Any]
todo_list_app/app/routes/task_routes.py:17:    response_model=dict
forge_blog_cms/app/routes/tag_routes.py:23:    response_model=Dict
```

**Single root cause**: the LLM's own backend-generation habit of
annotating a route's `response_model` as a bare `dict`/`Dict`/
`Dict[str, Any]` — a generic "don't bother with a real schema" escape
hatch, used across list/pagination endpoints and occasionally others —
combined with the handler's actual return value nesting a **raw,
unconverted SQLAlchemy ORM instance** (most commonly via
`query(...).all()`, whose result is a list of ORM objects, not
dictionaries or Pydantic models). `dict`/`Dict` as a type carries no
`from_attributes`/ORM-mode configuration at all, so FastAPI's serializer
has no idea how to turn a nested `Task`/`Tag`/`Note` object into JSON —
hence "Unable to serialize unknown type: <class 'app.models.X.Y'>".

Not multiple subclasses: every instance examined shares the exact same
two-part shape (dict-typed response_model + raw ORM object reachable
inside the returned value). Some instances self-heal across later
regenerations of the same project when the LLM happens to add an
explicit conversion (see `forge_blog_cms`'s *current* `tag_routes.py`,
which now calls `TagResponse.model_validate(tag, from_attributes=True)`
per item and no longer crashes) — this is the same root cause resolved
by chance in a later attempt, not a different failure class.

## 3. Representative reconstruction (real, on-disk project — no synthetic data)

`generated_projects/todo_list_app/app/routes/task_routes.py` (captured
live by Exp086, still un-repaired on disk):

```python
@task_router.get("/tasks", response_model=dict)
def list_tasks(..., db: Session = Depends(get_db), current_user=...):
    query = db.query(Task).filter(Task.user_id == current_user.id)
    ...
    items = query.offset(offset).limit(limit).all()   # raw Task ORM objects
    return {"items": items, "total": total}            # never converted
```

**Trace**:
- **ORM model** (`app/models/tasks.py`): `Task(Base)`, plain SQLAlchemy
  model, nothing wrong with it in isolation.
- **Response schema** (`app/schemas/tasks.py`): a properly-configured
  `TaskResponse(BaseSchema)` **exists**, where `BaseSchema` correctly
  sets `model_config = ConfigDict(from_attributes=True)`. This schema is
  simply never reached by the list endpoint.
- **Endpoint**: `response_model=dict` — the schema above is bypassed
  entirely.
- **Serialization**: FastAPI/Pydantic tries to JSON-encode the returned
  dict; the `items` key holds a `list[Task]` (ORM instances); `dict` as
  a type gives Pydantic zero ORM-mode context for anything nested inside.
- **Runtime exception**: `pydantic_core._pydantic_core.PydanticSerializationError:
  Unable to serialize unknown type: <class 'app.models.tasks.Task'>` —
  exact match to both recorded telemetry occurrences.

## 4. Why `ConfigDict(from_attributes=True)` is "absent" — corrected framing

It is **not** absent from the applicable schema class (`TaskResponse`
already has it, correctly, via `BaseSchema`). The existing auto-generated
diagnostic hint (*"Fix: add `ConfigDict(from_attributes=True)` to EVERY
Pydantic schema class..."*) is **imprecise for this exact failure
shape** — adding that config to `TaskResponse` would do nothing, since
`TaskResponse` is never invoked by this endpoint at all. The real gap is
that the **endpoint doesn't route its return value through any schema
class in the first place.**

## 5. Origin: backend generation (not planner/architecture/repair)

- **Planner/architecture**: neither dictates response_model types at
  this granularity — out of scope for this bug.
- **Backend generation**: **this is where it originates.** The LLM
  writes a bespoke `{"items": [...], "total": N}` pagination wrapper
  directly in the route handler and reaches for `dict`/`Dict` as a
  quick, valid-looking type annotation, without generating (or
  reusing) a dedicated `TaskListResponse`-style Pydantic wrapper schema.
  Confirmed as a generation-time habit, not repair-introduced: e.g.
  `simple_notes_app`'s `user_routes.py` uses `Dict[str, Any]` on
  a *non-list* endpoint too — this is a general "I'll just use dict"
  tendency, not something scoped only to pagination.
- **Repair infrastructure independently reinforces the same blind
  spot** (found while searching for task 7's "existing deterministic
  infrastructure," below) rather than causing the original instance.
- **Runtime rewrite**: not implicated in any of the 4 examined cases —
  the bug is present in each project's *generation-time* code, before
  any runtime-fix attempt touched these specific files.

## 6. Existing deterministic infrastructure found (Task 7) — a genuine extension candidate

Two existing patchers in `app/services/deterministic_patcher.py` already
grapple with exactly this class of problem, and both currently resolve
it by **weakening the type contract** rather than fixing the underlying
mismatch:

- **`_patch_orm_response_model()`** (line 881): when it can't match an
  ORM-typed `response_model=<OrmClass>` to a real schema class, its own
  fallback line reads `return f"response_model={prefix}dict{suffix}"  #
  fallback: dict serializes fine`. That comment's premise is exactly
  false for this bug — `dict` only "serializes fine" when nothing nested
  inside still needs ORM-mode conversion, which this function has no way
  of checking. This function *already* builds an `orm_classes` set (from
  `app/models/*` imports) and a `schema_map` (ORM class name → matching
  schema class + its import module) — precisely the lookup machinery
  needed to solve this properly instead of falling back.
- **`_patch_list_response_model_mismatch()`** (line 4024): when
  `response_model=List[X]` doesn't match a detected `return {"items":
  ...}` shape, it **strips the response_model annotation entirely**
  rather than fixing the shape — same underlying philosophy ("remove the
  type contract rather than repair it"), same blind spot to whether the
  return value itself still needs conversion.

**Smallest deterministic repair candidate**: extend
`_patch_orm_response_model()` (reusing its existing `orm_classes`/
`schema_map` construction, not duplicating it) to also scan each route
function's body for a `return {"items": <var>, ...}` pattern where
`<var>` was assigned from a `.query(...).all()` (or `.offset(...).limit(...).all()`)
call whose target class is a known ORM class with a matching entry in
`schema_map`. When found, inject a one-line conversion immediately before
the `return` — `<var> = [<SchemaCls>.model_validate(x, from_attributes=True) for x in <var>]`
— so the returned value is already JSON-safe regardless of what the
declared `response_model` says. This is additive (new detection +
injection only), reuses existing lookup machinery verbatim, and doesn't
touch either patcher's existing behavior for cases that don't match this
specific shape.

## 7. Estimated reliability impact

Lower single-instance frequency than the auth-thread bug (6 all-time vs.
9), but reproduced identically across **4 of 4 examined, independent app
categories** (todo, blog, recipe, notes) — this is a general backend-
generation habit, not a narrow edge case, and is very likely to recur in
any future app whose architecture includes a paginated list endpoint
(an extremely common pattern). Both recorded `generation_log.jsonl`
scores (65.9, 76.9) were capped below deploy-ready specifically by this
one failure — fixing it plausibly recovers similar per-instance score
gains to Exp085's auth fix, though on a smaller recorded sample; the
true prevalence is likely undercounted the same way Exp077 noted for
MissingEndpoint (self-heals silently in some runs, so telemetry
under-attributes it).

## 8. Recommendation for Exp088

Implement the extension described in §6, scoped to
`app/services/deterministic_patcher.py`'s `_patch_orm_response_model()`
only — no new patcher module, no changes to `_patch_list_response_model_mismatch()`
(different mismatch shape, out of scope). Offline-test against a
reconstructed fixture matching `todo_list_app`'s exact `task_routes.py`
shape (already captured, real, on disk — no synthetic data needed) before
any live validation.

**Deliverables**: this doc, `experiments.md` entry. No code changes, no
Cerebras calls. **Cost: $0.**
