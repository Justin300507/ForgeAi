# Runtime Failure Knowledge Base (Experiment 068, Part 7 — expanded by Experiment 069, Part 5)

2026-07-12. First written for Experiment 068; expanded for Experiment
069 with a **Status** and **Owner subsystem** field added to every
entry (no new investigation was re-run — both fields are derived from
the same evidence already cited in each entry below, per Exp069's own
"expand Exp068" instruction rather than redoing the research). Evidence
only — every field cites the file/data it came from. "Unknown" is used
explicitly wherever the evidence available doesn't establish an
answer; it is never silently omitted. Confidence labels: **High**
(directly verified by reading the actual code/data), **Medium**
(verified from a named function/file reference but not read in full,
or inferred from strong indirect evidence), **Low** (a single data
point, or a plausible reading not independently corroborated).

**Status** values used below: **Open** (still recurring as of the most
recent data, no confirmed fix), **Likely Resolved** (recent last_seen
close to a documented fix, not independently re-confirmed by a fresh
generation), **Resolved (durable)** (stale last_seen, 6+ days with zero
recurrence, strong evidence the fix held), **Needs Triage** (evidence
is ambiguous or contradictory, root cause itself unclear).

**Owner subsystem** values used below map to this project's own
architecture, not an invented taxonomy: **Generation** (V6 multi-agent
backend/frontend generation), **Validation** (the `app/services/*_validator.py`
family + `verification/engine.py`), **Repair** (`app/repair/orchestrator.py`,
`preflight.py`, `deterministic_patcher.py`), **Runtime** (`app/runtime/`,
the startup smoke test and journey runner), **Write Pipeline**
(`file_writer_service.py`/`fix_writer_service.py`, hardened in
Experiments 066-067).

Covers the 15 clusters with count≥3 (91% of the taxonomy's 194
classified instances) in full detail, plus a compact table for the
7 count=1 clusters not traced in Exp068 and not re-attempted here.

---

## MissingEndpoint

**Symptoms**: An endpoint listed in the architecture plan is never implemented in the corresponding route file. Manifests as a 404 at runtime, or — per `endpoint_validator.py`'s own docstring — a frontend page that "loads forever" when the frontend calls an endpoint that was never planned or implemented either.

**Root Cause**: Generation-stage — the LLM's backend-generation pass skips an endpoint the architecture plan promised. Category: **architecture-issue / generation-bug**.

**Detection**: Confirmed, two independent static checks in `app/services/endpoint_validator.py`: `validate_endpoints()` (lines 95-166) AST-compares the architecture plan's `api_endpoints` against actual `@router.<verb>()` decorators found via `extract_actual_backend_routes()` (lines 21-92); `validate_frontend_api_calls()` (lines 173-257) independently catches endpoints the *frontend* calls that were never planned or implemented on the backend at all.

**Repair**: Confirmed, LLM-driven, not deterministic — `app/services/missing_file_service.py::generate_missing_file()` (lines 12-49) synthesizes the missing route file via a dedicated prompt. **No deterministic/preflight patcher exists for this pattern.** No success-rate evidence found in code comments for `generate_missing_file`'s output quality — Unknown whether its generated routes reliably match the expected signature.

**Validation**: The two detectors above ARE the validation step; there is no separate post-repair validation confirmed beyond re-running the same checks in the next verification pass.

**History**: First seen 2026-06-22T07:49:29 (`patterns.json`), last seen 2026-07-11T23:12:06 — still occurring in the most recent data. 48 total instances, the largest cluster by volume.

**Experiments**: No experiment in `experiments.md` specifically targets `MissingEndpoint` end-to-end (Exp021's `RouterExportMismatch` fix is adjacent but distinct — a route file existing with the wrong export name, not a route file never generated at all).

**Confidence**: High (detection mechanism directly read and confirmed; repair mechanism directly read and confirmed absent of a deterministic path).

**Status**: Open — last_seen 2026-07-11, no fix shipped.

**Owner subsystem**: Generation (root cause) / Validation (detection, `endpoint_validator.py`) / Repair (partial, LLM-only path, no deterministic patcher).

**Update (Experiment 074, 2026-07-12)**: live re-confirmation of the
**non-auth sub-case** specifically — Exp071 resolved the auth sub-case
(`/auth/register` 404s, confirmed again this cycle: zero across every
attempt in a fresh 3-app canary), but a live `blog_cms` run this cycle
generated no `PUT`/`DELETE /posts/{id}` route at all (confirmed by
reading the final `post_routes.py` directly — only `GET`×3 and `POST`
exist), capping that app's score at 70.0/C on `Runtime Startup` and
`Integration` dimensions. Same root cause and same "no deterministic
repair path" gap this entry already documents — general CRUD
`MissingEndpoint` remains open and unfixed; not investigated further
this cycle per Exp074's own measurement-only rule. See
`docs/EXP074_VALIDATION.md` §3.

---

## JourneyCRUDFailure

**Symptoms**: The backend starts and passes static/build checks, but the live register→login→create→edit→delete user journey fails at some step. Concretely, from the 14 forensic bundles (all 2026-07-11): `POST /auth/register` → 404 (9/14, 64%); `PUT /products/{id}` → 405 (3/14, 21%); `POST` create → 400 `"Priority ID does not exist"` (1/14); a runner-targeting mismatch hitting `/stats/summary` instead of the real endpoint → 405 (1/14).

**Root Cause**: **Not one root cause — at least 4 distinct ones sharing one label.** The dominant sub-cause (64% of the richest recent evidence) is a **generation-bug**: the auth-registration route was never generated/wired, discovered only because the journey runner exercises it live. A second sub-cause (21%) is the same generation-bug pattern for an edit endpoint. A third is a seed/test-data FK-consistency issue. A fourth looks like a runner bug, not a generation defect at all.

**Detection**: Confirmed, runtime-only by nature — `app/runtime/user_journey_runner.py::run_user_journey()` (line 344+) does a live HTTP walk against a running backend, with OpenAPI-schema-driven request-body construction and missing-field retry logic. **No static pre-check exists or could exist for this class in general** — though the dominant sub-cause (missing `/auth/register`) is, in principle, exactly what `MissingEndpoint`'s existing static validators already catch for other routes; it is Unknown from this cycle's evidence why the auth routes specifically weren't caught by those same checks before reaching the live journey runner.

**Repair**: Confirmed **zero dedicated repair mechanism**. Grepped `app/repair/orchestrator.py` and `app/repair/preflight.py` for "journey"/"Journey": only comments referencing it (`orchestrator.py:297`, `preflight.py:539-541`), no dedicated patcher or fix function. Falls through entirely to the generic, undifferentiated LLM repair loop using the raw dominant_errors string as the diagnostic. The one bundle traceable to a `generation_log.jsonl` entry (FR-000014) shows `succeeded: false` even after `fix_count: 4` — the generic loop did not resolve it in 4 attempts.

**Validation**: The journey runner itself is the validation step; there is no separate confirmation pass.

**History**: First seen 2026-06-29T23:47:06, last seen 2026-07-11T21:38:03 — the most recent, richest evidence in the whole dataset (all 14 forensic bundles). 30 total instances in `patterns.json`, the second-largest cluster.

**Experiments**: Exp010 ("m1-journey-crud-fix", a 422-coercion fix) and Exp012/preflight's NOT-NULL-gap work are the closest prior attempts, per `preflight.py:539-541`'s own comment distinguishing them from a true journey-failure fix. Neither is a dedicated JourneyCRUDFailure repair mechanism. `crud_ok` in the canary history has never been `True` for the `todo` app across all 16 measured runs since Experiment 020 (see `docs/RUNTIME_HISTORY.md`).

**Confidence**: High for the detection/repair-gap finding (directly read); Medium for the 4-way bundle decomposition (14 bundles is a real but modest sample, all from a single day).

**Status**: Open — last_seen 2026-07-11, zero dedicated repair mechanism confirmed, `todo`'s `crud_ok` has never once been `True` in the canary history. Highest-priority open item in the whole taxonomy by combined volume and confirmed repair-gap.

**Owner subsystem**: Runtime (`user_journey_runner.py`, detection) / Generation (root cause for the dominant 64% sub-cause — a route never generated) / Repair (confirmed gap — no dedicated strategy exists).

**Update (Experiment 072, live validation)**: the dominant sub-cause
above (missing `/auth/register`) is **confirmed fixed** — a live
4-app canary run (`exp072-validation-r1`, `docs/EXP072_VALIDATION.md`)
found zero 404s on any auth endpoint across todo/blog_cms/crm/inventory,
versus 9/14 forensic bundles before. **`crud_ok` still did not pass**
for 3 of 4 apps, but for a **newly-identified, different root cause**:
`app/services/deterministic_patcher.py::_patch_attr_access_mismatches()`
has a scope-confusion bug (detection is class-specific, but its fix is
applied via a file-wide blanket `re.sub()`) that can rewrite a
correctly-injected auth template's own field reference into a broken
one — independently reproduced in both `todo` and `blog_cms` this run.
This is a **new, distinct cluster** from the original 4-way bundle
decomposition above (not previously named in `patterns.json`'s 21-pattern
taxonomy) — recommend a dedicated pattern key
(e.g. `PatcherScopeConfusionError` or similar) in a future experiment,
not retrofitted into this entry's own history to avoid conflating two
genuinely different bugs under one name.

---

## AttributeError

**Symptoms**: `'X' object has no attribute 'y'` at runtime — the codebase's own prevention rule (`_PATTERN_RULES["AttributeError"]`) names three known shapes: calling a method on a value that can be `str` or `None`, unguarded Optional relationship access, and shadowing a builtin/module name with a local variable.

**Root Cause**: Long-tail — many distinct dynamic-typing mistakes bucketed under one exception type, not one fixable bug.

**Detection**: Partial. `validate_undefined_symbols()` (`app/services/undefined_symbol_validator.py:26+`) and `validate_self_shadowing_functions()` (`app/services/self_shadow_validator.py:7+`) catch a subset (undefined names, shadowed builtins). Most `AttributeError` instances are almost certainly only caught via runtime traceback classification (`_classify_validation_error`'s `"attributeerror"` substring match) — no static check can catch e.g. `None.strftime()` at an arbitrary dynamic call site in general.

**Repair**: Confirmed, well-invested — `_patch_attr_access_mismatches` (`deterministic_patcher.py:3239`), `_patch_unsafe_model_hasattr_filter` (`:3177`), `_patch_ownership_fk_attribute_drift` (`:3328` — this project's own most recent commit at the time of this cycle), and `preflight.py::_fix_config_missing_attrs` for the Config-object-specific sub-case.

**Validation**: Re-run of the same detection checks after a patch fires.

**History**: First seen 2026-07-01T22:56:38, last seen 2026-07-11T23:23:01 — still current despite the repair investment. 18 total instances.

**Experiments**: Exp046 ("silent ownership-FK naming drift") is the most recent targeted fix, per this project's own commit history (`6777e37`).

**Confidence**: High (both detection and repair mechanisms directly read).

**Status**: Open — last_seen 2026-07-11 despite 3+ dedicated patchers already shipped; most likely a long tail of distinct causes under one exception type, not one fixable bug (diminishing returns on further narrow patchers, per `docs/RUNTIME_ROADMAP.md` recommendation #6's triage-before-patching suggestion).

**Owner subsystem**: Repair (`deterministic_patcher.py`, `preflight.py`) / Validation (partial, `undefined_symbol_validator.py`, `self_shadow_validator.py`).

---

## ImportError

**Symptoms**: `ImportError: cannot import name 'X' from 'Y'` — the codebase's own rule names the common case: importing a helper from another route file that doesn't actually define/export it.

**Root Cause**: Unclear from static evidence alone — likely a mix of generation-bug (LLM hallucinates an import) and long-tail runtime shapes.

**Detection**: **Not found.** No `validate_import*` function exists in any validator file checked this cycle. Almost certainly runtime-traceback-only (`_classify_validation_error`'s `"importerror"` substring match).

**Repair**: Narrow — `_patch_missing_pydantic_imports` (`deterministic_patcher.py:3645`) covers one specific sub-case; `preflight.py::fix_missing_init` (line 370) fixes missing `__init__.py` package markers, a common import-failure root cause. No general import-error fixer found.

**Validation**: Unknown — no dedicated post-fix check found distinct from re-running the general syntax/static pass.

**History**: First seen 2026-06-22T05:55:11, last seen 2026-07-11T23:12:06 — still current. 13 total instances.

**Experiments**: None found specifically targeting general ImportError as a category.

**Confidence**: Medium (grep-confirmed absence of a dedicated validator; the two named patchers were found but not read in full).

**Status**: Open — last_seen 2026-07-11, no dedicated static detection exists.

**Owner subsystem**: Repair (`preflight.py::fix_missing_init`, `deterministic_patcher.py::_patch_missing_pydantic_imports`, both narrow) — no Validation-layer ownership confirmed.

---

## ConfigAttributeError

**Symptoms**: `AttributeError: 'Config' object has no attribute 'X'` — accessing a `Settings`/`Config` attribute that was never defined.

**Root Cause**: Generation-bug — the LLM's `app/config.py` output omits an attribute that's referenced elsewhere in the generated app.

**Detection**: Not a distinct static check — same runtime-traceback classification as the general case (`_classify_validation_error`'s `"config attribute"` substring match).

**Repair**: Confirmed, dedicated, and unusually well-documented — `preflight.py::_fix_config_missing_attrs` (priority 14, starting line 223) whose own docstring documents two independently-discovered root causes it was extended to cover (a second Config instance built directly in `main.py`; a case-sensitivity mismatch between defined and read attribute names), with an explicit "confirmed live (2026-07-06 canary)" note written into the code itself.

**Validation**: Re-run of config-attribute access checks.

**History**: First seen 2026-07-05T09:26:46, last seen 2026-07-07T12:32:33 — **one day after the documented fix shipped**, and no occurrence since in this dataset. The strongest "likely solved" candidate in the whole taxonomy, with one caveat below.

**Experiments**: The `_fix_config_missing_attrs` extension referenced above (exact experiment number not separately confirmed this cycle — the code comment cites the date, not an experiment number).

**Confidence**: Medium — the fix itself is High-confidence (directly read, documented, self-confirmed live). Whether the pattern is now fully closed is Medium: **Unknown whether the 2026-07-07 last-seen instance predates the fix (stale data still in the window) or represents a residual edge case the fix doesn't cover** — not resolved by this cycle's evidence.

**Status**: Likely Resolved — 5 days stale as of this document's writing (2026-07-12), with a documented, self-confirmed-live fix directly explaining the timing. Per `docs/RUNTIME_ROADMAP.md` recommendation #14, a cheap fresh-generation re-check would upgrade this to Resolved (durable) with more confidence.

**Owner subsystem**: Repair (`preflight.py::_fix_config_missing_attrs`, priority 14) — no dedicated Validation-layer detection.

---

## SQLAlchemyError

**Symptoms**: `sqlalchemy.exc.*` errors at runtime — broad category covering ORM misconfiguration.

**Root Cause**: Generation-bug — malformed relationships, dangling foreign keys, or missing model imports in `main.py`'s metadata scope.

**Detection**: Confirmed, dedicated — `validate_database()` (`app/services/database_validator.py:24+`), `validate_orm_usage()` (`app/services/orm_validator.py:137+`), and `validate_no_flask_sqlalchemy()` (`orm_validator.py:36+`, catches Flask-SQLAlchemy-style usage in a plain-SQLAlchemy project).

**Repair**: Confirmed, extensive, and confirmed *actively firing in production* (all four appear as nonzero keys in real `generation_log.jsonl` `prevention_counts`) — `_patch_strip_relationships` (`:255`), `_patch_dangling_foreign_keys` (`:365`), `_patch_main_fk_imports` (`:429`), `_patch_strip_back_populates` (`:131`), all in `deterministic_patcher.py`.

**Validation**: Re-run of the same static ORM checks.

**History**: First seen 2026-06-21T21:30:53, last seen 2026-07-06T12:23:57 — 6+ days stale relative to the dataset's most recent entries, the best evidence of durability among high-volume clusters. 11 total instances.

**Experiments**: Multiple prior experiments across the ADR-001 relationship-extraction work (per this project's own history).

**Confidence**: High (both detection and repair directly read and confirmed firing).

**Status**: Resolved (durable) — 6+ days stale, the best-covered pattern in the taxonomy on both detection and repair.

**Owner subsystem**: Validation (`database_validator.py`, `orm_validator.py`) / Repair (`deterministic_patcher.py`, 4 dedicated patchers, all confirmed firing in real `generation_log.jsonl` telemetry).

---

## RouterExportMismatch

**Symptoms**: A route file exists but doesn't export the exact variable name (`{resource}_router`) `main.py` expects to import.

**Root Cause**: Generation-bug — naming drift between the route file's export and what `main.py`'s import statement expects.

**Detection**: `_patch_router_export_mismatch` (`deterministic_patcher.py:4379`) is confirmed as a repair function; a separately-named static *detection* function was not confirmed distinct from it in the time available — **Unknown whether detection and first-pass repair are combined in this one function** (a common pattern in this codebase's preflight style) or genuinely separate.

**Repair**: Confirmed, two dedicated patchers — `_patch_router_names` (`:1375`) and `_patch_router_export_mismatch` (`:4379`), plus `preflight.py::fix_router_names` (priority 25).

**Validation**: Re-import check after patching.

**History**: First seen 2026-06-21T20:43:34, last seen 2026-07-06T01:13:44. 9 total instances.

**Experiments**: Experiment 021 (this project's own history — "deterministic RouterExportMismatch repair"), directly correlated with a canary `runtime_ok` False→True flip for `todo` (see `docs/RUNTIME_HISTORY.md`).

**Confidence**: Medium (repair confirmed; detection mechanism specifics not fully resolved).

**Status**: Resolved (durable) — 6 days stale as of Exp068's data; correlated with a confirmed canary `runtime_ok` False→True flip for `todo` at Experiment 021.

**Owner subsystem**: Repair (`deterministic_patcher.py`, `preflight.py`) — Validation-layer ownership Unknown (per Exp068's own finding, not re-investigated here).

---

## ModuleNotFoundError

**Symptoms**: `ModuleNotFoundError: No module named 'X'` at startup.

**Root Cause**: Generation-bug — a referenced module was never created, or a package directory is missing its `__init__.py`.

**Detection**: **Not found** as a dedicated static check — same runtime-traceback-only pattern as `ImportError`.

**Repair**: `preflight.py::fix_missing_init` (line 370) is the closest dedicated fix. Otherwise falls to the generic LLM loop.

**Validation**: Unknown — not separately confirmed.

**History**: First seen 2026-06-21T21:19:46, last seen 2026-07-01T19:49:17 — 10+ days stale relative to the dataset's most recent entries. 9 total instances.

**Experiments**: None found specifically targeting this category as a whole.

**Confidence**: Medium.

**Status**: Resolved (durable) — 10+ days stale, no dedicated static check but the narrow repair appears sufficient for the volume observed.

**Owner subsystem**: Repair (`preflight.py::fix_missing_init`) — no Validation-layer ownership confirmed.

---

## FrontendBuildError

**Symptoms**: `vite build failed` — JSX syntax errors, broken imports, icon-export mismatches.

**Root Cause**: Generation-bug — malformed JSX (e.g. this project's own previously-fixed template-literal class-name bug) or a frontend import referencing a nonexistent file/export.

**Detection**: Not traced to a distinct pre-build static check this cycle — Unknown whether one exists separate from the Vite build itself failing (which is, by nature, both the trigger and the detection signal for this category — plausibly not a gap at all, just how build-time failures inherently work).

**Repair**: Confirmed, multiple dedicated patchers — `preflight.py::fix_frontend_missing_imports` (line 471), `_patch_broken_template_literal_classname(s)` (`deterministic_patcher.py:1659`, `:1756` — this project's own prior JSX-build-break fix), `_patch_dedupe_frontend_imports` (`:4785`).

**Validation**: Re-run of the Vite build itself.

**History**: First seen 2026-06-21T20:43:34, last seen 2026-06-30T00:47:12 — 12+ days stale, a durably-improved category. 7 total instances.

**Experiments**: This project's own prior template-literal/JSX-nested-brace fixes.

**Confidence**: Medium (repair confirmed with multiple patchers; detection specifics genuinely Unknown, not just unread).

**Status**: Resolved (durable) — 12+ days stale.

**Owner subsystem**: Repair (`preflight.py`, `deterministic_patcher.py`) / Generation (frontend generation, root cause).

---

## NoReferencedTableError

**Symptoms**: SQLAlchemy can't resolve a foreign key's target table at `Base.metadata.create_all()` time.

**Root Cause**: Generation-bug — a model referenced by a foreign key was never imported into `main.py`'s metadata scope.

**Detection / Repair**: Shared with `SQLAlchemyError` above (same validator/patcher family) — not independently re-verified as a distinct check this cycle.

**History**: First seen 2026-06-21T20:43:34, last seen **2026-06-27T14:53:33 — the oldest (stalest) last-seen date of any cluster with count≥3 in the entire taxonomy.** 6 total instances.

**Experiments**: Same family as `SQLAlchemyError`.

**Confidence**: High for "durably fixed" (staleness itself is strong evidence); Medium for the exact mechanism (grouped with SQLAlchemyError, not independently traced).

**Status**: Resolved (durable) — the stalest last-seen date of any cluster with count≥3 in the entire taxonomy.

**Owner subsystem**: Validation (`database_validator.py`, `orm_validator.py`) / Repair (`deterministic_patcher.py`), shared with `SQLAlchemyError`.

---

## SyntaxError

**Symptoms**: A generated `.py` file fails `ast.parse()` — commonly a query-string literally embedded as part of a module filename/import path (this project's own previously-fixed bug shape), unmatched parentheses, or bad indentation.

**Root Cause**: Generation-bug (malformed LLM output) or repair-bug (a previous fix attempt wrote invalid Python — this project's own prior-experiment history found and fixed exactly this failure mode once already, Experiment 054).

**Detection**: **Confirmed, directly verified this session** (not inference) — `ast.parse()` inside `_is_safe_to_write()` in both `app/services/file_writer_service.py` and `app/services/fix_writer_service.py`, read and tested as part of this project's own Experiment 066/067 work earlier in this session.

**Repair**: Inline auto-repair attempts exist in `_is_safe_to_write()` itself: `_repair_backslash_syntax`, `_fix_indent_error`, `_fix_fastapi_param_order`, tried in sequence before giving up and skipping the file.

**Validation**: The `ast.parse()` re-check after each repair attempt IS the validation step.

**History**: First seen 2026-06-21T20:43:34, last seen 2026-07-11T23:27:43 — still recent. 6 total instances, but the stored examples' specific "querystring-as-filename" shape was already root-caused and fixed in this project's prior history (`project_querystring_route_bug`). **Unknown whether the 2026-07-11 last-seen instance is a recurrence of the already-fixed shape or a genuinely new, not-yet-identified syntax-error variant** — not resolved without inspecting a fresh generation.

**Experiments**: Experiment 006 (querystring-as-filename fix, commit 4af31b4) and Experiment 054/057 (repair-time syntax validation, this session's own prior work).

**Confidence**: High for the detection mechanism (directly tested this session); Low for whether the most recent instance is the same bug recurring or a new one.

**Status**: Needs Triage — detection is solid and confirmed working, but whether the 2026-07-11 recurrence is the already-fixed variant regressing or a new variant is genuinely unresolved.

**Owner subsystem**: Write Pipeline (`_is_safe_to_write()` in both writer services, hardened further in Experiments 066-067) / Repair (inline auto-repair attempts).

---

## PydanticSerializationError

**Symptoms**: A `response_model`-typed endpoint returns something that doesn't serialize cleanly against its declared schema.

**Root Cause**: Generation-bug — schema/ORM-object field mismatch.

**Detection**: **Not found** as a dedicated static check this cycle.

**Repair**: Confirmed, heavily invested — `_patch_orm_response_model` (`:881`), `_patch_response_schemas_optional` (`:2674`), `_patch_response_schema_inherited_required_fields` (`:2818`), `_patch_from_orm`/`_patch_pydantic_orm_mode` (`:2390`, `:3000`), all in `deterministic_patcher.py`.

**Validation**: Unknown, not separately confirmed.

**History**: First seen 2026-06-29T23:39:46, last seen 2026-07-11T12:46:44 — still current despite repair investment. 5 total instances.

**Experiments**: None individually named for this specific category.

**Confidence**: Medium.

**Status**: Open — last_seen 2026-07-11 despite 4+ dedicated repair patchers; detection gap is the most likely reason the repair investment hasn't closed it.

**Owner subsystem**: Repair (`deterministic_patcher.py`) — no Validation-layer ownership found.

---

## NotNullViolationError

**Symptoms**: `IntegrityError` on INSERT — a `nullable=False` column with no default wasn't supplied a value by the create path.

**Root Cause**: Generation-bug — model/schema/frontend field-requiredness drift.

**Detection/Repair**: `preflight.py::_fix_model_schema_notnull_gap` (priority 24) — this project's own Experiment 012 fix; detection and repair appear combined in the same function (not separately verified this cycle).

**History**: First seen 2026-07-01T19:37:49, last seen 2026-07-06T10:02:36. 4 total instances.

**Experiments**: Experiment 012 (NOT NULL gap fix) and its Experiment 013 requiredness refinement.

**Confidence**: Medium (fix is historically well-documented in this project's own record; internal mechanism not independently re-verified this cycle).

**Status**: Resolved (durable) — 6 days stale, historically significant fix (Exp012/013).

**Owner subsystem**: Repair (`preflight.py::_fix_model_schema_notnull_gap`, priority 24).

**Update (Experiment 074, 2026-07-12)**: a **distinct sub-case, not
covered by the Exp012/013 fix above**, observed live in an `inventory`
canary run — `PUT /products/{id}` (full-replace update) wrote `sku=None`
because the request payload omitted it, raising the same
`IntegrityError: NOT NULL constraint failed` shape, but on the
**update/replace** path, not the **create** path this entry's fix
targets (`_fix_model_schema_notnull_gap`'s own description is explicitly
create-path: "wasn't supplied a value by the create path"). Self-resolved
this run via the generic LLM repair loop (3 attempts, final score
90.9/A), not by any deterministic patcher — so this Status line's
"Resolved (durable)" should be read as **create-path only**; the
update-path variant is new evidence, not yet triaged into its own
cluster. Flagged as the smaller/higher-ROI of Exp074's two candidate
targets for Exp075. See `docs/EXP074_VALIDATION.md` §3-4.

---

## ValidationError

**Symptoms**: Pydantic validation failure at request or response time.

**Root Cause**: Unknown — possibly overlapping with `ResponseValidationError`/`PydanticSerializationError` rather than a genuinely distinct category.

**Detection**: Not found as a dedicated static check.

**Repair**: Likely overlaps with the `PydanticSerializationError` patcher family listed above — not confirmed whether this is the same underlying issue bucketed under two different taxonomy keys, or genuinely distinct. `_classify_validation_error()`'s substring checks for `"validationerror"` vs. `"responsevalidationerror"` (`failure_memory.py:262-298`) are ordered so the more specific string is checked first, which *should* prevent misclassification, but this was not traced through an actual raw error string this cycle.

**History**: First seen 2026-06-21T23:42:14, last seen 2026-07-01T17:05:10. 3 total instances.

**Confidence**: Low — flagged as a taxonomy-precision question for a future experiment, not resolved here.

**Status**: Needs Triage — root cause and even cluster identity itself are unresolved pending taxonomy disambiguation (see `docs/RUNTIME_ROADMAP.md` recommendation #7).

**Owner subsystem**: Unknown — possibly Repair (if it turns out to overlap with `PydanticSerializationError`'s patcher family), not confirmed.

---

## RelationshipModelNotImported

**Symptoms**: A SQLAlchemy relationship references a model that was never imported into scope.

**Root Cause**: Generation-bug, same family as `SQLAlchemyError`/`NoReferencedTableError`.

**Detection/Repair**: Likely `_patch_main_fk_imports` (`deterministic_patcher.py:429`), same family as above — not independently confirmed by reading its body this cycle.

**History**: First seen 2026-06-22T10:44:11, last seen **2026-06-22T11:22:25 — same day, never recurred since.** 3 total instances.

**Confidence**: Medium (staleness is strong "solved" evidence; exact mechanism not independently re-verified).

**Status**: Resolved (durable) — never recurred since its single day of occurrence (2026-06-22), 20+ days stale.

**Owner subsystem**: Validation / Repair (SQLAlchemy family, shared with `SQLAlchemyError`/`NoReferencedTableError`).

---

## Long tail (count=1 each, not traced this cycle)

| Cluster | First/last seen | Prevention rule exists? | Status (by staleness only, mechanism unverified) |
|---|---|---|---|
| TimestampNotNullError | 2026-06-22 (single occurrence) | Yes, in `_PATTERN_RULES` | Resolved (durable) by staleness |
| MonolithicSchemaError | 2026-06-22 (single occurrence) | Yes | Resolved (durable) by staleness |
| FastAPIError | 2026-06-22 (single occurrence) | No dedicated rule text | Resolved (durable) by staleness |
| RelationshipMissingError | 2026-06-27 (single occurrence) | Yes | Resolved (durable) by staleness |
| UserIdNotInjectedError | 2026-06-28 (single occurrence) | No dedicated rule text | Resolved (durable) by staleness |
| InvalidDependsType | 2026-06-30 (single occurrence) | Yes | Resolved (durable) by staleness |
| ModelFieldMismatchError | 2026-07-01 (single occurrence) | No dedicated rule text | Needs Triage — only 11 days stale, less confidence than the others |
| ResponseValidationError | 2 occurrences, 2026-07-05 to 2026-07-06 | No dedicated rule text (shares family with ValidationError/PydanticSerializationError) | Needs Triage — recency + taxonomy-overlap concern with ValidationError |

Whether corresponding detection/repair code exists for each was **not verified** this cycle — explicitly out of scope given the time-budget prioritization decision (the 15 clusters above cover 91% of classified instances). "Status by staleness only" means no repair mechanism was confirmed to exist or fire — the inference rests entirely on zero recurrence over time, weaker evidence than the count≥3 clusters above where a repair mechanism was also directly verified.

**Owner subsystem for all 8 long-tail clusters**: Unknown — not traced this cycle (Experiment 069, Part 5), same as Experiment 068's own scope limit.
