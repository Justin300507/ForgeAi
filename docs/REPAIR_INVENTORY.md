# ForgeAI Repair Pipeline — Full Inventory

Experiment 051 (Reliability Debt Audit), 2026-07-11. Read-only
investigation, $0, no generation, no LLM calls, no prompt changes.

**114 functions** across 10 files, matching the naming convention
`^def _?(patch|fix)_\w+`. All but one (`_llm_fix`, flagged below) are
**deterministic** — pure regex/string/AST-adjacent transforms with no
model call. Trigger/summary text below is drawn directly from each
function's own docstring or leading comment where one exists; a `—` means
no docstring/comment was present and the one-line description is this
audit's own reading of the function body (marked *[read]*) or, where not
individually re-read in this pass, the function's name and signature
alone (marked *[name-inferred]* — treat these as lower-confidence and
verify before relying on them).

Companion documents: `REPAIR_GRAPH.md` (execution order, dispatch
mechanisms, ordering dependencies) and `REPAIR_DEBT.md` (risk ranking,
duplication, dead-code check).

Legend — **Tests**: ✅ has a dedicated test referencing this function by
name, ❌ none found (`backend/tests/` grepped for the literal name).
**Dispatch**: which of `REPAIR_GRAPH.md` §1's four mechanisms invokes it.

---

## 1. `app/services/deterministic_patcher.py` (66 functions)

Dispatch mechanism 1 (hardcoded sequential list) unless noted. Full
execution order in `REPAIR_GRAPH.md` §2-§3.

| Function | Line | Inputs → Outputs | Trigger / summary | Tests | Dispatch |
|---|---|---|---|---|---|
| `_patch_wrong_auth_module` | 41 | `content: str` → `str` | *[name-inferred]* fixes an import pointing at the wrong auth module | ❌ | run_deterministic_patches (inline chain) |
| `_patch_passlib` | 71 | `content: str` → `str` | *[name-inferred]* passlib→bcrypt swap in source content | ❌ | inline chain |
| `_patch_requirements` | 95 | `req_path: Path` → `None` | *[name-inferred]* requirements.txt normalization | ❌ | run_deterministic_patches (per requirements.txt) |
| `_patch_strip_back_populates` | 129 | `project_path: Path` → `int` | Strips residual `back_populates=`/`backref=` kwargs; defensive backstop for `_patch_strip_relationships` | ❌ | run_deterministic_patches |
| `_patch_strip_relationships` | 253 | `project_path: Path` → `int` | Strip ALL `relationship()` attribute declarations from SQLAlchemy model files (prevents `NoForeignKeysError` mapper crash) | ❌ | run_deterministic_patches (runs first in the relationship family, see `REPAIR_GRAPH.md` §2) |
| `_patch_dangling_foreign_keys` | 363 | `project_path: Path` → `int` | If a `ForeignKey("X.id")` references a table with no model file, strips it (prevents `NoReferencedTableError`) | ❌ | run_deterministic_patches |
| `_patch_main_fk_imports` | 427 | `project_path: Path` → `None` | *[name-inferred]* fixes FK-related imports in main.py; must run after alias patches | ❌ | run_deterministic_patches |
| `_patch_async_sync` | 522 | `content, filepath` → `str` | *[name-inferred]* async/sync ORM call mismatch fix | ❌ | inline chain |
| `_patch_circular_schema_imports` | 580 | `content, filepath` → `str` | Removes `from app.routes.*` imports from schema files to break circular deps | ❌ | inline chain |
| `_patch_model_aliases` | 599 | `project_path: Path` → `None` | Wave 2.5 renames plural class names (Games→Game) only inside model files; this propagates the alias elsewhere | ❌ | run_deterministic_patches |
| `_patch_deduplicate_models` | 847 | `project_path: Path` → `int` | *[name-inferred]* dedupes `user.py`/`users.py`-style model file collisions | ❌ | run_deterministic_patches |
| `_patch_deduplicate_schemas` | 851 | `project_path: Path` → `int` | Same singular/plural collision as models, for schema files; must run before import-redirect patcher | ❌ | run_deterministic_patches |
| `_patch_orm_response_model` | 879 | `content, filepath, project_path` → `str` | *[name-inferred]* ORM-vs-response-model type fix | ❌ | inline chain |
| `_patch_pydantic_regex` | 963 | `content: str` → `str` | Pydantic v2 removed `regex=` kwarg from `Field()` — replace with `pattern=` | ❌ | inline chain |
| `_patch_func_name_vs_label` | 980 | `content: str` → `str` | *[name-inferred]* | ❌ | inline chain |
| `_patch_smart_quotes` | 1015 | `content: str` → `str` | Unicode smart quotes → ASCII | ❌ | inline chain (first in the chain) |
| `_patch_relationship_string_aliases` | 1025 | `project_path: Path` → `None` | Fix SQLAlchemy `relationship()` string references using the wrong class name (Genre→Genres); must run before FK import patcher | ❌ | run_deterministic_patches |
| `_patch_param_order` | 1174 | `project_path: Path` → `int` | Reorder route params to fix "non-default argument follows default argument" | ❌ | run_deterministic_patches |
| `patch_reorder_shadowed_static_routes` | 1247 | `project_path: str` → `int` | A static sub-route registered AFTER a parameterized route with the same shape is permanently unreachable | ❌ | run_deterministic_patches |
| `_patch_router_names` | 1335 | `project_path: Path` → `int` | `router` → `{resource}_router`, eliminates `RouterExportMismatch` | ❌ | run_deterministic_patches |
| `_patch_pagination_component` | 1555 | `project_path: Path` → `int` | Glob all `Pagination.jsx` under `src/`, inject known-good template if the file looks like the standard component (widened Exp049, 2026-07-11) | ❌ | run_frontend_patches |
| `_patch_broken_template_literal_classname` | 1619 | `content: str` → `tuple[str, int]` | Exp049: detect/collapse a broken template-literal-ternary `className` to a static string | ✅ `test_broken_template_literal_classname.py` | called by `_patch_broken_template_literal_classnames` |
| `_patch_broken_template_literal_classnames` | 1716 | `project_path: Path` → `int` | Project-wide file walker for the function above | ❌ (covered transitively) | run_frontend_patches |
| `_patch_auth_utils` | 1734 | `project_path: Path` → `None` | *[name-inferred]* known-good auth.py injection | ❌ | run_deterministic_patches (skippable) |
| `_patch_auth_requirements` | 1761 | `project_path: Path` → `None` | *[name-inferred]* ensures auth-related packages in requirements.txt | ❌ | run_deterministic_patches (skippable) |
| `_patch_forward_role_to_duplicate_registrars` | 2086 | `project_path: Path` → `int` | Some generated apps have a SECOND, LLM-authored registration endpoint; forwards role-vocabulary awareness to it | ✅ `test_role_forward_patcher.py` | called from *inside* `_patch_auth_routes` (line 2183) — not in the main dispatcher list, see `REPAIR_GRAPH.md` §4 |
| `_patch_auth_routes` | 2141 | `project_path: Path` → `None` | Inject a known-good `auth_routes.py` if the project has a `User` model but broken/missing auth routes | ❌ | run_deterministic_patches (skippable) |
| `_patch_seed_robustness` | 2234 | `project_path: Path` → `int` | Wrap every `_create_X(db, ...)` seed helper in try/except | ❌ | run_deterministic_patches |
| `_patch_depends_body` | 2332 | `content, filepath` → `str` | Remove `= Depends()` from Pydantic schema body params in route files | ❌ | inline chain |
| `_patch_from_orm` | 2350 | `content: str` → `str` | *[name-inferred]* `.from_orm()` v1→v2 fix | ❌ | inline chain |
| `_patch_create_missing_schemas` | 2444 | `project_path: Path` → `int` | Create minimal Pydantic schema files when route files import from a missing module | ❌ | run_deterministic_patches |
| `_patch_response_schemas_optional` | 2634 | `project_path: Path` → `int` | For every class ending Response/Out/Read/Schema/List/Detail, widen ORM field-name mismatches to Optional | ✅ `test_response_schema_inheritance.py` | run_deterministic_patches |
| `_patch_response_schema_inherited_required_fields` | 2778 | `project_path: Path` → `int` | Same fix for fields a `*Response` class INHERITS from a shared `*Base` rather than declaring itself — `_patch_response_schemas_optional` only sees a class's own body text | ✅ `test_response_schema_inheritance.py` | run_deterministic_patches |
| `_patch_schemas_from_attributes` | 2887 | `project_path: Path` → `int` | Inject `model_config = {'from_attributes': True}` into every Pydantic BaseModel | ❌ | run_deterministic_patches |
| `_patch_pydantic_orm_mode` | 2960 | `content: str` → `str` | Replace Pydantic v1 `orm_mode=True` with v2 `from_attributes` | ❌ | inline chain |
| `_patch_star_dict_extra_fields` | 3001 | `project_path: Path` → `int` | Replace `Model(**schema.dict(), ...)` with a filtered dict | ❌ | run_deterministic_patches |
| `_patch_filtered_ctor_kwarg_collision` | 3084 | `project_path: Path` → `int` | Fix an already-filtered constructor call missing exclusion for a kwarg colliding with a same-named schema field | ❌ | run_deterministic_patches |
| `_patch_unsafe_model_hasattr_filter` | 3137 | `project_path: Path` → `int` | Rewrite `if hasattr(Model, k)` to `if k in Model.__table__.columns.keys()` (a read-only `@property` passing through `hasattr` raises on write) | ❌ | run_deterministic_patches |
| `_patch_attr_access_mismatches` | 3199 | `project_path: Path` → `int` | Fix route files accessing `obj.invalid_attr` that doesn't exist on the model | ❌ | run_deterministic_patches |
| `_patch_ownership_fk_attribute_drift` | 3281 | `project_path: Path` → `int` | Fix `ModelClass.wrong_ownership_field` in query/filter expressions (Exp046, silent data-isolation bug) | ✅ `test_ownership_fk_drift.py` | run_deterministic_patches |
| `_patch_missing_create_update_fields` | 3442 | `project_path: Path` → `int` | Add a field to Create/Update schema when a handler reads it but a sibling schema already declares it | ✅ `test_missing_create_update_fields.py` (+2 more) | run_deterministic_patches |
| `_patch_missing_pydantic_imports` | 3598 | `project_path: Path` → `int` | Ensure schema files using BaseModel/Field/Optional actually import them | ❌ | run_deterministic_patches |
| `_patch_orm_type_in_route_schemas` | 3716 | `project_path: Path` → `int` | Replace SQLAlchemy model types used inside Pydantic class bodies in route files | ❌ | run_deterministic_patches |
| `_patch_list_response_model_mismatch` | 3796 | `project_path: Path` → `int` | Fix `response_model=List[X]` on handlers returning `{"items":..., "total": N}` | ❌ | run_deterministic_patches |
| `_patch_frontend_package_json` | 3938 | `project_path: Path` → `bool` | Scan `src/*.jsx` for npm imports, add any missing packages to package.json | ❌ | run_frontend_patches |
| `_patch_create_missing_service_stubs` | 4073 | `project_path: Path` → `int` | *[name-inferred]* creates service-layer stubs when routes import a missing `app.services.X` | ❌ | run_deterministic_patches |
| `_patch_missing_db_refresh` | 4174 | `project_path: Path` → `int` | Inject `db.refresh(obj)` after `db.commit()` where missing (POST returns `id=None` otherwise) | ❌ | run_deterministic_patches |
| `_patch_wire_orphan_routers` | 4223 | `project_path: Path` → `None` | Scan for every `*_router = APIRouter(...)`, wire unwired ones into main.py | ❌ | run_deterministic_patches |
| `_patch_router_export_mismatch` | 4325 | `project_path: Path` → `int` | Fixes "Router export mismatch... Expected 'Y_router'" | ❌ | **not** in run_deterministic_patches — called directly from `v6_orchestrator.py:441,1086` (see `REPAIR_GRAPH.md` §6) |
| `patch_ensure_auth_pages` | 4585 | `project_path: Path` → `int` | Synthesizes LoginPage/RegisterPage if App.jsx redirects to `/login` but the LLM never generated them | ❌ | run_deterministic_patches **and** run_frontend_patches — runs twice per generation, see `REPAIR_DEBT.md` Risk 6 |
| `_patch_dedupe_frontend_imports` | 4731 | `project_path: Path` → `None` | Remove duplicate default-import declarations of the same identifier | ❌ | run_deterministic_patches |
| `_patch_wire_orphan_frontend_routes` | 4771 | `project_path: Path` → `None` | Frontend mirror of `_patch_wire_orphan_routers`: App.jsx routinely imports a page never mounted on a `<Route>` | ❌ | run_deterministic_patches |
| `_patch_login_redirect_target` | 4953 | `project_path: Path` → `int` | Fix hardcoded `/dashboard` refs when the app's main page isn't literally named "Dashboard" | ❌ | run_deterministic_patches |
| `_patch_schema_nullable_required_mismatch` | 5020 | `project_path: Path` → `int` | Required schema field on a nullable model column → `Optional[T] = None` | ❌ | run_deterministic_patches |
| `_patch_response_schema_id_and_datetimes` | 5135 | `project_path: Path` → `int` | Two guaranteed-broken response patterns: missing `id`, DateTime typed as `str` | ❌ | run_deterministic_patches |
| `_patch_disallowed_icon_packages` | 5434 | `project_path: Path` → `int` | Rewrite `@heroicons/react` imports to `lucide-react` equivalents | ❌ | run_frontend_patches |
| `_patch_redirect_missing_backend_imports` | 5525 | `project_path: Path` → `int` | Redirect `from app.X import ...` to wherever the symbol actually lives; must run before router wiring | ❌ | run_deterministic_patches |
| `_patch_hidden_loading_status` | 5654 | `project_path: Path` → `int` | Hoist a retry/wake-up status message out of the not-loading branch | ❌ | run_frontend_patches |
| `_patch_frontend_auth_field_names` | 5706 | `project_path: Path` → `int` | Fix LoginPage/RegisterPage reading `.username`/`.id` off the auth response | ❌ | run_frontend_patches |
| `_patch_frontend_signup_password_key` | 5753 | `project_path: Path` → `int` | Fix RegisterPage sending `hashed_password` instead of `password` | ❌ | run_frontend_patches |
| `_patch_stale_status_on_error` | 5797 | `project_path: Path` → `int` | Clear an in-flight retry-status message when a real error occurs | ❌ | run_frontend_patches |
| `_patch_invalid_lucide_icons` | 5855 | `project_path: Path` → `int` | Replace lucide-react icon imports the pinned package version doesn't export | ✅ `test_icon_validity.py` | run_frontend_patches |
| `_patch_missing_icon_imports` | 5941 | `project_path: Path` → `int` | Add lucide-react/react-router-dom imports used but never imported | ❌ | run_frontend_patches |
| `_patch_unsafe_optional_chain_before_array_method` | 6043 | `project_path: Path` → `int` | Fix `x?.y.map(...)` — optional-chained on the base but not before `.map` | ❌ | run_frontend_patches |
| `_patch_response_data_used_as_bare_array` | 6088 | `project_path: Path` → `int` | Fix `res.data.map(...)` assuming a bare-array axios response body | ❌ | run_frontend_patches |
| `_patch_response_data_assumed_wrapped` | 6141 | `project_path: Path` → `int` | Fix `res.data.items` assuming a wrapped axios response | ❌ | run_frontend_patches |

## 2. `app/repair/preflight.py` (16 `_fix_*` functions + registry)

Dispatch mechanism 2 (`PreflightRegistry`, priority order). Signature for
all 16: `(project_path: Path, diagnostics: list) → bool`. Priority
determines execution order — see `REPAIR_GRAPH.md` §5 for the full
ordered list and the source-vs-priority-order divergence.

| Function | Line | Priority | Trigger / summary | Tests |
|---|---|---|---|---|
| `_fix_pyjwt` | 93 | 10 | Add PyJWT if any file `import jwt`s but it's not in requirements | ❌ |
| `_fix_bcrypt` | 112 | 11 | Add bcrypt if `utils/auth.py` uses it but it's missing from requirements | ❌ |
| `_fix_config_missing_settings_instance` | 149 | 13 | Ensure `app/config.py` exports a module-level `settings` instance | ❌ |
| `_fix_postgres_url` | 177 | 15 | `postgres://` → `postgresql://` in database.py (SQLAlchemy 1.4+ requirement) | ❌ |
| `_fix_config_missing_attrs` | 207 | 14 | Ensure commonly-referenced settings attrs (DATABASE_URL, SECRET_KEY, ...) exist | ❌ |
| `_fix_missing_init` | 354 | 20 | Add missing `__init__.py` files in `app/` subdirectories | ❌ |
| `_fix_query_param_basemodel` | 367 | 22 | Every `Query(...)`-defaulted param needs a valid FastAPI dependant type | ❌ |
| `_fix_frontend_missing_imports` | 455 | 23 | The recurring `Could not resolve "./Navbar"` class of build error | ❌ |
| `_fix_model_schema_notnull_gap` | 506 | 24 | Model-generation wave and schema/route-generation wave can disagree on NOT NULL | ❌ |
| `_fix_router_names` | 640 | 25 | Rename bare `router = APIRouter()` to `{resource}_router = APIRouter()` | ❌ |
| `_fix_param_order` | 651 | 26 | Reorder route params: body before Path/Query/Depends | ❌ |
| `_fix_missing_env` | 662 | 30 | Generate a `.env` skeleton with sensible defaults if missing | ❌ |
| `_fix_strip_passlib_imports` | 701 | 35 | Remove passlib/werkzeug imports (incompatible libraries) | ❌ |
| `_fix_cors_missing` | 721 | 40 | Add CORS middleware to main.py if missing | ❌ |
| `_fix_missing_health_endpoint` | 753 | 45 | Add `GET /health` to main.py if missing | ❌ |
| `_fix_database_py` | 767 | 50 | Inject known-good database.py if missing or broken | ❌ |

**Fact:** every one of these 16 has zero direct test coverage. `preflight.run()`
itself (the registry's dispatch method) is likewise untested.

## 3. `app/services/database_patcher.py` (8 functions)

Dispatch mechanism: **none** — each called individually by name from 3+
different call sites (`core/pipeline.py`, `repair/orchestrator.py`,
`services/v6_orchestrator.py`). No aggregator function exists for this
file, unlike `deterministic_patcher.py` or `preflight.py`. See
`REPAIR_DEBT.md` Risk 3 for the consequence: only `patch_database_py` is
called from the repair-loop's per-attempt cleanup; the other 5 are not.

| Function | Line | Inputs → Outputs | Trigger / summary | Tests |
|---|---|---|---|---|
| `patch_database_py` | 148 | `project_path: str` → `bool` | Overwrite `app/database.py` with a known-good template | ❌ |
| `_patch_main_py_duplicate_engine` | 173 | `app_dir: Path` → `None` | Strip module-level engine/SessionLocal/Base duplicates from main.py | ❌ |
| `_patch_main_py_create_all` | 216 | `app_dir: Path` → `None` | Replace `Base.metadata.create_all(...)` in main.py with `create_tables()` | ❌ |
| `patch_model_field_mismatches` | 336 | `project_path: str` → `int` | Route files' SQLAlchemy constructor calls using field names the model doesn't have | ❌ |
| `patch_add_missing_model_columns` | 525 | `project_path: str` → `int` | Route constructors pass a field the model genuinely has no column for | ❌ |
| `patch_missing_required_constructor_kwargs` | 889 | `project_path: str` → `int` | Model column `nullable=False` with no default, but the route constructor never passes it | ❌ |
| `patch_filter_dict_unpack_constructor_kwargs` | 1057 | `project_path: str` → `int` | `Model(**some_dict)` crashes with an invalid-keyword TypeError | ❌ |
| `patch_add_missing_schema_fields` | 1174 | `project_path: str` → `int` | Route handlers read a field off a Create/Update schema that was never declared there | ❌ |

## 4. `app/services/deployed_fixer.py` (5 deterministic + 1 LLM function)

Dispatch mechanism 4 (inline if/elif in `fix_deployed_app`, line 179).

| Function | Line | Inputs → Outputs | Trigger / summary | Tests | Deterministic |
|---|---|---|---|---|---|
| `_fix_cors` | 27 | `main_py_path: Path` → `bool` | *[name-inferred]* | ❌ | Yes |
| `_fix_auth_utils` | 69 | `project_path: Path` → `bool` | *[name-inferred]* | ❌ | Yes |
| `_fix_auth_routes` | 81 | `project_path: Path` → `bool` | Regenerate `auth_routes.py` from a known-good template | ❌ | Yes |
| `_fix_requirements` | 108 | `project_path: Path` → `bool` | *[name-inferred]* | ❌ | Yes |
| `_llm_fix` | 132 | `error: dict, project_path: Path` → `Optional[dict]` | Falls through to an LLM call (`generate_content`, stage="deployed_fix") when the deterministic fixes above don't cover the error | ❌ | **No** — the only non-deterministic function in this inventory |
| `fix_deployed_app` | 179 | dispatcher | Routes a live-deployment check-result to the right fixer by `etype`/status code | ❌ | (dispatcher, not itself a fix) |

## 5. `app/services/deployment_fix_service.py` (6 functions)

Dispatch mechanism 3 (`_DETERMINISTIC_FIXES` dict, `@_deterministic_fix("ErrorType")`
decorator, line 25). All deterministic — the LLM call in this file
(`generate_content`, line 285) lives in a *different*, unrelated function
(`generate_deployment_fix`) not matching this inventory's naming filter.

| Function | Line | Inputs → Outputs | Trigger / summary | Tests |
|---|---|---|---|---|
| `_fix_health_check` | 36 | `project_path: str, parsed_error: dict` → `dict \| None` | *[name-inferred]* | ❌ |
| `_fix_port_error` | 49 | same | *[name-inferred]* | ❌ |
| `_fix_frontend_build` | 56 | same | Deterministic fixes for npm/Cloudflare Pages build failures | ❌ |
| `_fix_cloudflare_build` | 118 | same | Cloudflare Pages-specific fixes | ❌ |
| `_fix_render_timeout` | 149 | same | Render deployment timeouts usually mean the app failed to bind to `$PORT` | ❌ |
| `_fix_requirements` | 173 | same | *[name-inferred]* — note: same function name as `deployed_fixer.py`'s `_fix_requirements`, different file, different signature; not the same function (see `REPAIR_DEBT.md` duplication section — not independently re-verified for logic overlap in this pass) | ❌ |

## 6. `app/services/file_writer_service.py` (6 functions)

Dispatch: chained inline inside `_normalize_newlines` and `_is_safe_to_write`
(both called from `write_files`, the per-file write hook — not one of the
four mechanisms in `REPAIR_GRAPH.md` §1, a fifth, narrower pattern).

| Function | Line | Inputs → Outputs | Trigger / summary | Tests |
|---|---|---|---|---|
| `_fix_indent_error` | 61 | `content: str` → `str` | Fix "unexpected indent" SyntaxError by dedenting the offending block | ❌ |
| `_fix_pydantic_v1_patterns` | 153 | `content: str` → `str` | Fix Pydantic v1 patterns that crash under v2 | ❌ |
| `_fix_smart_quotes` | 178 | `content: str` → `str` | Unicode smart quotes/dashes → ASCII in JSX/JS files — **note:** a second, separately-defined `_fix_smart_quotes`-equivalent exists as `deterministic_patcher.py::_patch_smart_quotes`; not confirmed identical logic in this pass, flagged as a duplication candidate not yet resolved | ❌ |
| `_fix_double_depends` | 191 | `content: str` → `str` | `Depends(Depends(X))` → `Depends(X)` | ❌ |
| `_fix_fastapi_param_order` | 247 | `content: str` → `str` | Path/Query/Depends params appearing before body params — **note:** conceptually the same bug class as `deterministic_patcher.py::_patch_param_order` and `preflight.py::_fix_param_order`; a THIRD implementation of param-order fixing, not resolved in this pass | ❌ |
| `_fix_schemas_namespace` | 394 | `content: str` → `str` | Detect `schemas.resource.ClassName` patterns, rewrite to direct imports | ❌ |

**This file surfaces the clearest additional duplication lead found in
this pass beyond the already-tracked JSX/template-literal family:**
param-order fixing exists in three places
(`deterministic_patcher.py::_patch_param_order`,
`preflight.py::_fix_param_order`, `file_writer_service.py::_fix_fastapi_param_order`)
and smart-quote fixing in at least two
(`deterministic_patcher.py::_patch_smart_quotes`,
`file_writer_service.py::_fix_smart_quotes`). Not resolved — flagged for
`REPAIR_DEBT.md`'s duplication section as a lead for the next cycle, same
treatment as the already-documented JSX family (found, cited, not
consolidated — that would be a behavior change).

## 7. `app/services/frontend_service.py` (3 functions)

Dispatch: sequential chain at generation time (lines 178-180, quoted in
`REPAIR_DEBT.md`'s duplication section). Called once per frontend file as
it's written, before `deterministic_patcher.py`'s repair-stage patchers
ever run.

| Function | Line | Inputs → Outputs | Trigger / summary | Tests |
|---|---|---|---|---|
| `_fix_jsx_brace_errors` | 13 | `content: str` → `str` | Fix `style={{...}}}>` — 3 closing braces instead of 2 before a tag-close | ❌ |
| `_fix_empty_template_expressions` | 26 | `content: str` → `str` | Strip empty `${}` interpolations (unrecoverable, trades a syntax error for a blank substring) | ❌ |
| `_fix_jsx_truncated_templates` | 37 | `content: str` → `str` | Close unclosed `${...}` / unclosed backtick template literals, line-by-line — **see `REPAIR_DEBT.md`: unconfirmed lead that this may convert Exp049's bug shape rather than fix it** | ❌ |

## 8. `app/services/project_service.py`, `runtime_fix_service.py`, `app/utils/json_cleaner.py` (4 functions)

| Function | File:line | Inputs → Outputs | Trigger / summary | Tests | Dispatch |
|---|---|---|---|---|---|
| `_patch_arch_fix_routes_into_main` | `project_service.py:58` | `project_path: str, written_paths: list` → `None` | After architecture repair, wire newly written route files into main.py | ❌ | called at `project_service.py:631` (architecture-repair flow) |
| `_fix_unresolvable_dependency` | `runtime_fix_service.py:40` | `runtime_error, project_path` → (fix or `None`) | Regex-matches `pip`'s "Could not find a version..." error, deterministic shortcut tried before falling through to the LLM-driven `generate_runtime_fix` | ❌ | called at `runtime_fix_service.py:99`, first check inside `generate_runtime_fix` |
| `_fix_path_backslashes` | `json_cleaner.py:5` | `match` (regex Match object) → replacement str | Inside a "path" field specifically, every backslash needs different escaping treatment than free text | ❌ | passed as a bare callback to `re.sub(pattern, _fix_path_backslashes, text)` at line 329 — **not** invoked with a literal call, invisible to naive call-site grep |
| `_fix_triple_quoted_content` | `json_cleaner.py:22` | *[name-inferred]* | *[name-inferred]* JSON-cleaning helper for triple-quoted string content | ❌ | not independently traced in this pass |

---

## Coverage summary

- **114 functions** inventoried across 10 files.
- **8 (7.0%)** have any direct test reference.
- **1 (`_llm_fix`)** is non-deterministic (calls an LLM); all other 113 are pure deterministic transforms.
- **0** confirmed dead — see `REPAIR_DEBT.md`'s "Non-finding" section for the 3 indirect-dispatch patterns that produced false positives before being ruled out.
- **5 dispatch mechanisms** in total once `file_writer_service.py`'s inline-chain pattern is counted alongside `REPAIR_GRAPH.md` §1's four.

## Methodology / what this document is NOT

This inventory was built by direct source extraction (function
signatures, docstrings, leading comments) plus targeted call-site greps —
not by executing the pipeline, not by generating a test app, and not by
reading every function's full body line-by-line. Entries marked
*[name-inferred]* were not individually re-read in this pass; their
one-line description is inferred from the function's name and signature
alone and should be verified before being relied on for anything beyond
this survey. A new engineer should treat this document as a map, not a
substitute for reading the ~15 functions relevant to whatever they're
actually changing.
