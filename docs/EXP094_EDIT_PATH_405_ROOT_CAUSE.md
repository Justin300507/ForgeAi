# Experiment 094 — Root Cause Investigation of JourneyCRUDFailure (Edit / 405)

2026-07-13. Investigation only, $0, zero Cerebras calls, zero code
changes. Follows Exp091's methodology: root-cause via real generated
projects, saved architecture metadata, and the actual test-harness code
— no reconstruction, no speculation.

## 1. Collected failures (Task 1)

21 `JourneyCRUDFailure` bundles exist in `failure_memory/bundles/`
(bundle-level logging started 2026-07-11, so this only covers the most
recent window, not the full historical population). Grouped by exact
`(step, status_code, method)`:

| Step | Status | Method | Count | Projects |
|---|---|---|---|---|
| Register | 404 | POST | 9 | todo_list_app (1 generation run, repeated retries) |
| **Edit entity** | **405** | **PUT** | **6** | inventory_manager (3), forge_blog_cms (3) |
| Create entity | 400 | POST | 4 | inventory_manager |
| Create entity | 405 | POST | 1 | todo_list_app |
| Edit entity | 422 | PUT | 1 | inventory_manager |

**Important normalization** (same lesson as Exp084's counter bug — raw
bundle counts overstate distinct incidents): the 6 Edit/405 bundles are
not 6 independent generations. Timestamps show inventory_manager's 3
bundles span 23:28:40–23:29:56 (76s) and forge_blog_cms's 3 span
09:51:48–09:53:21 (93s) — each cluster is **one generation run's repair
loop retrying 3 times and failing identically each time** (0%
same-run self-heal, consistent with Exp090's finding for the Create-path
bug). So the true distinct-run count for this exact bug in the bundle
sample is **2 runs**, not 6.

## 2. Symptom taxonomy (Task 2)

- **HTTP 405** (this experiment's target): 6/6 Edit-path bundles. Body
  is always `{"detail": "Method Not Allowed"}` — a pure FastAPI routing
  response, not application code executing at all.
- **Wrong route method**: this *is* the 405 mechanism, not a separate
  bucket (see §3).
- **Missing entity_id**: 0 direct Edit/405 hits show this — a different
  symptom text ("no entity_id captured") appears in *older*,
  non-bundled `generation_log.jsonl` entries, but that's a downstream
  symptom of the already-closed Create-path bug (Exp091-093), not an
  Edit-path defect. Out of scope, not conflated here.
- **Parameter mismatch**: 1 case (`Edit entity 422`,
  `inventory_manager/products/1` — `unit_cost`/`reorder_threshold`
  "Field required"). Different root cause (the `*Update` Pydantic
  schema wasn't fully optional, so a partial-update payload fails
  validation) — explicitly out of scope per this task's framing
  ("405 or fails to resolve the target entity"), noted honestly rather
  than folded in.
- **Route conflict**: 0 observed.
- **Other**: 0.

## 3. Trace through the pipeline (Tasks 3, 4)

Traced `inventory_manager` (`/products/6`) and `forge_blog_cms`
(`/posts/1`) plus two live, currently-on-disk confirmations
(`sports_league_manager`, `volunteer_management_system`, §5) end to end:

1. **Planner** (`app/prompts/planner_prompt.py:193-196`) documents the
   canonical CRUD template as `PUT /resources/{id}` for Update.
2. **Architect** (`app/prompts/architect_prompt.py:84-88, 216-220`)
   *also* templates Update as `PUT /[resources]/[id]` — but line 68
   separately states: `ONLY use HTTP methods: GET, POST, PUT, PATCH,
   DELETE` — a blanket allow-list meant to forbid WebSocket-style
   methods, with no scoping that restricts PATCH to action/sub-endpoints
   (`/publish`, `/unpublish`) versus the primary resource update. The
   architect LLM is therefore free to choose PATCH for the *canonical*
   update endpoint and sometimes does — confirmed directly in saved
   `metadata.json` architecture for `sports_league_manager`
   (`PATCH /leagues/{id}`, `PATCH /teams/{id}`, `PATCH /players/{id}` —
   **no PUT declared anywhere in the entire architecture**) and
   `teamflow_pm` (`API_DOCS.md`: `PATCH /api/tasks/{task_id}`, matching
   its saved architecture exactly).
3. **Backend generation** faithfully implements whatever the
   architecture declared — **not a divergence point**. Confirmed:
   `sports_league_manager/app/routes/league_routes.py:149` implements
   `@league_router.patch("/leagues/{league_id}", ...)`, structurally
   identical to a correct PUT handler (fetch-or-404, apply
   `model_dump(exclude_unset=True)` via `setattr` loop, commit, refresh,
   return) — just decorated with `.patch` instead of `.put`, exactly
   matching what the architecture asked for.
4. **Repair pipeline**: 0/3 retries fixed either failing run
   (`prevention_counts` for the inventory_manager run show every
   relevant patcher at 0 activations for this failure — nothing in
   `deterministic_patcher.py` currently targets HTTP-verb selection).
5. **Runtime / test harness** — `app/runtime/user_journey_runner.py`:
   - `_detect_crud_entity()` (line 290) selects the CRUD test entity by
     grouping architecture-declared endpoints by first path segment and
     preferring one whose *aggregate* method set is a superset of
     `{GET, POST, PUT, DELETE}` (line 316), falling back to just
     `{GET, POST}` if no resource has full PUT-inclusive CRUD (line
     326-330).
   - `do_edit()` (line 862-881) **hardcodes `requests.put(...)`** with
     no PATCH fallback and no lookup of which method the architecture
     actually declared for that entity's update route.

**Earliest divergence point**: the **architect prompt itself**
(`architect_prompt.py:68`), which grants unscoped permission to choose
PATCH for any endpoint including the canonical per-entity update route,
while its own template (and the planner's) models Update as PUT. This
is not a backend-generation bug (code faithfully implements the
architecture) and not strictly a generation bug at all — PATCH is a
legitimate, spec-compliant HTTP verb for partial updates. The **actual
runtime failure is caused by `user_journey_runner.py`'s hardcoded
PUT-only assumption**, which has no way to learn or fall back to
whatever method the architecture (which it already receives as a
parameter) actually declared.

## 4. Successful vs. failing handler comparison (Task 5)

`inventory_manager/app/routes/product_routes.py:83` (passes):
```python
@product_router.put("/products/{product_id}", response_model=ProductResponse, status_code=status.HTTP_200_OK)
def update_product(product_in: ProductUpdate, product_id: int = Path(..., ge=1), ...):
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Not found")
    for field, value in product_in.model_dump(exclude_unset=True).items():
        setattr(product, field, value)
    db.commit(); db.refresh(product)
    return ProductResponse.model_validate(product, from_attributes=True)
```

`sports_league_manager/app/routes/league_routes.py:149` (would 405 if
journey-tested — `leagues` is exactly the resource `_detect_crud_entity`
would select for this project, confirmed by direct replay, §5):
```python
@league_router.patch("/leagues/{league_id}", response_model=LeagueBase)
def update_league(league_in: LeagueUpdate, league_id: int = Path(..., gt=0), ...):
    league = db.query(Leagues).filter(Leagues.id == league_id).first()
    if not league:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="League not found")
    ...
    for field, value in league_in.model_dump(exclude_unset=True).items():
        setattr(league, field, value)
    db.commit(); db.refresh(league)
    return LeagueBase.from_orm(league)
```

**The two handlers are structurally identical in every way that
matters** (auth dependency, 404-on-missing, partial-update-via-setattr,
commit/refresh/return). The *only* difference is the decorator method
name — `.put` vs `.patch` — confirming the defect is purely a
verb-selection/test-harness mismatch, not a code-quality or
logic defect.

## 5. Existing deterministic infrastructure search (Task 6)

Searched `app/services/endpoint_validator.py`, `router_export_validator.py`,
`deterministic_patcher.py`, and `app/runtime/user_journey_runner.py` itself:

- `endpoint_validator.py`'s `validate_endpoints()` / `validate_orphan_routes()`
  check that declared endpoints exist and routers are wired — they do
  **not** check HTTP-verb agreement between architecture and test harness.
- `deterministic_patcher.py` has no existing patcher touching HTTP verb
  choice on generated routes (confirmed via `grep -i "put\|patch" `
  across the file — only unrelated hits like `_patch_param_order`).
- **The one directly relevant, already-existing piece of infrastructure
  is `_detect_crud_entity()` itself** — it already receives the full
  `architecture` dict (which contains the ground-truth declared method
  for every endpoint) but only extracts a resource *name*, discarding
  the per-endpoint method information before `do_edit()` ever runs.
  Extending this one function (or adding a sibling helper) to also
  surface "which method did the architecture declare for this entity's
  update route" is the natural, smallest extension point — no new
  infrastructure needed, no generated-code patching needed.

**Independently reproduced, not just theorized**: ran the actual
`_detect_crud_entity()` / `_detect_api_prefix()` functions (imported
directly, unmodified) against all 49 currently-on-disk projects'
saved architecture. Result: **2/49 (4.1%) would deterministically 405
today** if journey-tested with their current saved architecture:
  - `sports_league_manager` → selects `leagues` (`GET`, `PATCH`, `POST`
    aggregate — no PUT anywhere in the whole entity, not even on a
    sibling sub-path).
  - `volunteer_management_system` → selects `events`, whose aggregate
    method set is just `{GET, POST}` — **no update endpoint at all**,
    not even PATCH (confirmed: `event_routes.py` has only `GET /events`,
    `POST /events`, `GET /events/{id}`, `POST /events/{id}/publish`).
    This is a distinct sub-cause (architecture never declared an update
    endpoint for this resource) rather than a PUT/PATCH verb mismatch,
    but produces the identical observable 405 symptom.
  - 2 more projects (`todomaster`, `user_management_system`) have an
    **empty saved architecture** in `metadata.json` (`{}` /
    `api_endpoints: None`) — an unrelated, pre-existing metadata-
    persistence gap from an older generation format, flagged but not
    investigated (out of scope).

A broader corpus scan (raw endpoint-path grouping, less precise than
the exact-function replay above but useful for prevalence context) found
**11/49 (22.4%) of projects have at least one PATCH-only update endpoint
somewhere in their architecture** — most of those don't happen to be the
resource `_detect_crud_entity` selects (so they don't currently manifest
as a live 405), but they represent latent risk: any regeneration where
the architect's random verb choice lands on the *selected* entity
reproduces this bug immediately, matching exactly what happened to
`inventory_manager` and `forge_blog_cms` in the 2026-07-11/12 bundle
sample (their *current* regenerations happen to have PUT and therefore
don't show the bug today — consistent with this series' repeatedly-
confirmed "generated_projects only reflects the latest regen" limitation
masking a still-present, non-deterministically-triggered risk).

## 6. Frequency quantification (Task 7)

- **Direct bundle evidence**: 2 distinct generation runs (6 bundles,
  inflated 3x each by repair-loop retries) out of ~103 total logged
  generation attempts in the current `generation_log.jsonl` window —
  a lower bound, since bundle-level logging only started 2026-07-11.
- **Current-snapshot exact replay**: 2/49 (4.1%) of all currently-saved
  architectures would 405 today with their exact saved endpoint list.
- **Latent-risk corpus scan**: 11/49 (22.4%) of projects have at least
  one PATCH-only update endpoint in their architecture — the pool from
  which a future regeneration could unluckily draw the exact resource
  `_detect_crud_entity` selects, reproducing the bug non-deterministically
  run to run (same LLM-instruction-following-variance pattern already
  established for the Create-path bug in Exp091).

## 7. Proven root cause (summary)

The architect stage (`architect_prompt.py:68`) legally permits PATCH as
an update-endpoint verb without scoping that permission away from the
canonical per-entity update route, while the same prompt's own template
(and the planner's) models Update as PUT. When the architect's random
choice for the *specific entity the journey runner will select* lands on
PATCH — or omits an update endpoint for that entity entirely
(`volunteer_management_system`) — `user_journey_runner.py`'s `do_edit()`
(hardcoded to `requests.put()`, with no knowledge of the
architecture-declared method it already has access to) reports a false
`JourneyCRUDFailure`/405 against otherwise completely correct,
spec-compliant generated code. This is **not a generation defect** in
the cases confirmed here — it is a test-harness assumption gap.
(`volunteer_management_system`'s missing-update-endpoint case is a
distinct, smaller sub-bucket that a verb-fallback fix alone would not
address — flagged, not solved, here.)

## 8. Smallest deterministic implementation candidate

Extend `_detect_crud_entity()` (or add a sibling helper) in
`app/runtime/user_journey_runner.py` to also return, for the selected
entity, which of `PUT`/`PATCH` the architecture actually declared for its
`{id}`-suffixed update route (prefer PUT if both present, else PATCH),
and have `do_edit()` call `requests.request(update_method, ...)` instead
of the hardcoded `requests.put(...)`. Zero changes to generated code,
zero new patcher, reuses data the function already receives — the
smallest possible fix, and it corrects a false-failure rather than
constraining the architect's (legitimate) verb choice. Does not address
`volunteer_management_system`'s "no update endpoint at all" sub-case;
that would need a genuinely new patcher or architecture-completeness
check and is not the majority shape (1/49 confirmed vs. sports_league_manager-style
2/49 total, so the PUT/PATCH mismatch dominates the confirmed sample).

## 9. Estimated reliability improvement

Directly fixes the confirmed 2/49 (4.1%) current-snapshot false-positive
rate outright, and removes the latent risk represented by the 11/49
(22.4%) PATCH-containing-architecture pool for all future regenerations
of those ideas — converting a currently-0%-self-heal false failure
(repair loop cannot fix a test-harness bug by editing generated code, as
directly observed in both bundle-log runs) into a $0, deterministic
non-issue. Comparable in category to Exp088's `PydanticSerializationError`
fix: a correctness gap in ForgeAI's own verification path rather than in
the applications it generates.

## 10. Recommendation for Exp095

Implement the smallest candidate from §8 (test-harness method-detection
fix in `user_journey_runner.py`), offline-validated first against the
already-confirmed fixture shapes (`sports_league_manager`'s
`leagues`/`teams`/`players`, `inventory_manager`'s known-good PUT case as
a no-regression control), before any live canary. This is a test-harness
correction, not a generated-code patcher — no new
`deterministic_patcher.py` function is needed, keeping the fix
consistent with this cycle's "reuse existing infrastructure" mandate.
Recommend NOT attempting `volunteer_management_system`'s
missing-update-endpoint sub-case in the same cycle — it's a different
defect shape (architecture completeness, not verb selection) with only
1 confirmed instance so far; revisit if a future taxonomy re-scan shows
it recurring.

**Deliverables**: this doc, `experiments.md` entry. **Cost: $0, zero
Cerebras calls, zero code changes.**
