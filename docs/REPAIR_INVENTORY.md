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

*Enriched 2026-07-11 (same audit cycle): the original pass of this section
left many entries as `[name-inferred]` (not individually verified). A
dedicated line-by-line re-read of the entire file replaced every one of
those with a verified trigger/mechanism description, and surfaced two
additional confirmed findings folded into `REPAIR_DEBT.md`: (1)
`_patch_relationship_string_aliases`'s search target is structurally
eliminated by `_patch_strip_relationships` earlier in the same call — it
is called but can never find anything to fix; (2) the AST-usage count (4
of 66) and cross-module coupling point. No `[name-inferred]` entries
remain below.*

| Function | Line | Inputs → Outputs | Trigger / mechanism | Tests | Dispatch |
|---|---|---|---|---|---|
| `_patch_wrong_auth_module` | 41 | `content: str` → `str` | `from app.utils.(jwt_utils\|jose\|security\|auth_utils\|jwt\|token_utils) import ...` present → regex-rewrites the import path to `app.utils.auth` | ❌ | inline chain |
| `_patch_passlib` | 71 | `content: str` → `str` | literal `"passlib"` present → strips the import + `CryptContext` def, rewrites `.hash()`/`.verify()` calls to bcrypt, injects `import bcrypt` | ❌ | inline chain |
| `_patch_requirements` | 95 | `req_path: Path` → `None` | requirements.txt exists → line-filter drops `passlib*` lines, ensures a `bcrypt` line exists | ❌ | run_deterministic_patches (per requirements.txt found) |
| `_patch_strip_back_populates` | 129 | `project_path: Path` → `int` | any model file has `back_populates=`/`backref=` → regex strips the kwarg only, leaving the `relationship()` call itself; defensive backstop for the next row | ❌ | run_deterministic_patches |
| `_patch_strip_relationships` | 253 | `project_path: Path` → `int` | any model file contains `relationship(` → **unconditionally** removes the whole assignment (paren-depth tracked) and re-injects a session-backed `@property` per stripped relationship (many-to-one vs one-to-many resolved via a FK map; falls back to `[]`/`None` if unresolvable) — prevents `NoForeignKeysError` mapper crash | ❌ | run_deterministic_patches (runs first in the relationship family) |
| `_patch_dangling_foreign_keys` | 363 | `project_path: Path` → `int` | a `ForeignKey("X.id")` references a table with no model file defining it → regex removes the `ForeignKey(...)` call, cleans up stray commas/parens | ❌ | run_deterministic_patches |
| `_patch_main_fk_imports` | 427 | `project_path: Path` → `None` | a model's FK references a table whose module isn't imported in main.py → builds a table→module map from `__tablename__`, injects missing import lines (real class name resolved via regex, capitalize-join fallback) | ❌ | run_deterministic_patches |
| `_patch_async_sync` | 522 | `content, filepath` → `str` | `async def` present with no real `await` in a route handler, or sync ORM/`db: Session` usage inside an `async def` → strips `async` (route files use a stricter blanket rule than non-route files) | ❌ | inline chain |
| `_patch_circular_schema_imports` | 580 | `content, filepath` → `str` | path contains `/schemas/` AND content has `from app.routes.` → regex strips those import lines | ❌ | inline chain |
| `_patch_model_aliases` | 599 | `project_path: Path` → `None` | a non-model file imports a class name from `app.models.X` that doesn't exist there → 3-tier: singular/plural fuzzy alias assignment, then re-export from a fuzzy-matching sibling module, then (last resort) inject a stub `class Name(Base)` with just `id` | ❌ | run_deterministic_patches |
| `_dedupe_class_files` (helper) | 743 | `target_dir: Path, kind: str` → `int` | a singular/plural filename pair both exist (`user.py`+`users.py`) sharing an exact or variant class name → exact-name: keep the longer file; variant-name: keep whichever has more `Column(...)` declarations (deliberately not raw length — a stub's re-export scaffolding can inflate length past the real file), aliases the dropped class name to the kept one | ❌ | called from the two rows below |
| `_patch_deduplicate_models` | 847 | `project_path: Path` → `int` | thin wrapper: `_dedupe_class_files(app/models, "model")` | ❌ | run_deterministic_patches |
| `_patch_deduplicate_schemas` | 851 | `project_path: Path` → `int` | thin wrapper: `_dedupe_class_files(app/schemas, "schema")`; must run before the import-redirect patcher | ❌ | run_deterministic_patches |
| `_patch_orm_response_model` | 879 | `content, filepath, project_path` → `str` | a route file uses an ORM class as `response_model=` or a return-type annotation → builds an ORM-class→matching-Pydantic-schema map (name-prefix heuristic), rewrites to the matched schema (or bare `dict` fallback), injects the import | ❌ | inline chain |
| `_patch_pydantic_regex` | 963 | `content: str` → `str` | literal `"regex="` present → `regex=` → `pattern=` (Pydantic v1→v2 kwarg rename) | ❌ | inline chain |
| `_patch_func_name_vs_label` | 980 | `content: str` → `str` | content has both `"func."` and `".name("` → paren-depth scan finds `func.xxx(...).name(` chains specifically, rewrites `.name`→`.label` | ❌ | inline chain |
| `_patch_smart_quotes` | 1015 | `content: str` → `str` | unconditional → `str.translate()` on a fixed smart-quote/em-dash→ASCII map | ❌ | inline chain (first in the chain) |
| `_patch_relationship_string_aliases` | 1025 | `project_path: Path` → `None` | a model file has `relationship("X", ...)` where X isn't a real class name → fuzzy singular/plural match against real class names, rewrites the string | ❌ | run_deterministic_patches (direct) — **verified structurally unreachable: `_patch_strip_relationships` runs earlier in the same call and unconditionally removes every `relationship(...)` call first, so this function's search target cannot exist by the time it runs. Called every time, finds nothing every time. See `REPAIR_DEBT.md`.** |
| `_patch_param_order` | 1174 | `project_path: Path` → `int` | a route file fails `compile()` with "non-default argument follows default argument" (files that already compile are fast-skipped) → paren-depth-aware param-list extraction and reorder | ❌ | run_deterministic_patches (direct) — one of 3 independent implementations of this fix, see `REPAIR_DEBT.md` duplication section |
| `patch_reorder_shadowed_static_routes` | 1247 (+ helper `_route_shadows` at 1222) | `project_path: str` (⚠ `str`, not `Path` — the only sibling with this signature) → `int` | an earlier-registered parameterized route (`/x/{id}`) has the identical segment shape as a later static route (`/x/streaks`), making the static one permanently unreachable at Starlette's registration order → structural per-route-block segment comparison, reorders blocks | ❌ | run_deterministic_patches |
| `_patch_router_names` | 1335 | `project_path: Path` → `int` | an `APIRouter()` assignment variable isn't `{resource}_router` → whole-word rename in the route file, propagates into main.py's import + `include_router()` | ❌ | run_deterministic_patches |
| `_patch_pagination_component` | 1555 | `project_path: Path` → `int` | any `src/**/Pagination.jsx` (glob, widened Exp049) containing `"currentPage"` and differing from the canonical template → full-file replace with a hardcoded known-good template, no attempt to preserve custom logic | ❌ | run_frontend_patches |
| `_patch_broken_template_literal_classname` | 1619 | `content: str` → `tuple[str, int]` | Exp049: a JSX template-literal `className` with a dropped/empty `${` before a ternary → line-scan detect + collapse to a static className string (trades conditional styling for guaranteed-valid syntax) | ✅ `test_broken_template_literal_classname.py` | called only from `_patch_broken_template_literal_classnames` |
| `_patch_broken_template_literal_classnames` | 1716 | `project_path: Path` → `int` | per-file wrapper walking every `*.jsx` | ❌ (covered transitively) | run_frontend_patches |
| `_patch_auth_utils` | 1734 | `project_path: Path` → `None` | `auth.py` missing, or has passlib/werkzeug/python_jose, or missing `get_current_user`/`verify_password` → full-file replace with a known-good bcrypt+PyJWT template | ❌ | run_deterministic_patches (conditional on `not skip_protected_injections`) |
| `_patch_auth_requirements` | 1761 | `project_path: Path` → `None` | requirements.txt exists → drops passlib/python-jose lines, ensures PyJWT + bcrypt present | ❌ | run_deterministic_patches (conditional) |
| `_patch_forward_role_to_duplicate_registrars` | 2086 | `project_path: Path` → `int` | a non-auth route file imports `_make_user` and calls it with the exact 3-arg shape `_make_user(X.email, X.password, X.display_name)` → appends a 4th `getattr(X,'role',None)` arg | ✅ `test_role_forward_patcher.py` | called only from inside `_patch_auth_routes` (conditional on `needs_inject and role_info`), not in either aggregator directly |
| `_patch_auth_routes` | 2141 | `project_path: Path` → `None` | project has a User model AND (`auth_routes.py` missing or lacks the `_read_password` sentinel) → full-file replace with a parameterized known-good template (optionally role-aware), wires main.py | ❌ | run_deterministic_patches (conditional) |
| `_patch_seed_robustness` | 2234 | `project_path: Path` → `int` | `seed_routes.py` exists → wraps every `_create_X(db,...)` in try/except+rollback; injects `if not var:` guards before `var[0]` index accesses | ❌ | run_deterministic_patches |
| `_patch_depends_body` | 2332 | `content, filepath` → `str` | route file has literal `"Depends()"` → `param: SchemaClass = Depends()` → `param: SchemaClass` | ❌ | inline chain |
| `_patch_from_orm` | 2350 | `content: str` → `str` | content has `"from_orm"` → `.from_orm(x)` → `.model_validate(x, from_attributes=True)` | ❌ | inline chain |
| `_infer_fields_from_route_return` (helper) | 2380 | `routes_dir: Path, cls_name: str` → `list[tuple[str,str]]` | **uses real `ast.parse`/`ast.walk`** — finds a handler decorated `response_model=<cls_name>`, extracts fields from its `return {...}`/constructor call | ❌ | called only from `_patch_create_missing_schemas` |
| `_patch_create_missing_schemas` | 2444 | `project_path: Path` → `int` | a route does `from app.schemas.X import ClassName` where X.py doesn't define it → derives fields from a matching model's columns (falling back to the AST helper above), always includes `id: int`, writes/appends the schema | ❌ | run_deterministic_patches |
| `_patch_response_schemas_optional` | 2634 | `project_path: Path` → `int` | a `*Response`/`*Out`/`*Read`/`*List`/`*Detail`/`*Schema` class has a required field declared in its OWN body → widens to `Optional[T] = None` | ✅ `test_response_schema_inheritance.py` | run_deterministic_patches — complementary pair with the next row (own-body vs inherited fields, docstring-documented) |
| `_field_rhs_has_real_default` (helper) | 2749 | `rhs: str` → `bool` | distinguishes `Field(min_length=1)` (still required) from `Field(default=...)` (has a real default) | ❌ | called only from the row below |
| `_patch_response_schema_inherited_required_fields` | 2778 | `project_path: Path` → `int` | a `*Response`-named class INHERITS a required field via its base class without redeclaring it | ✅ `test_response_schema_inheritance.py` | run_deterministic_patches |
| `_patch_schemas_from_attributes` | 2887 | `project_path: Path` → `int` | a `BaseModel`-based class has no `model_config`/`from_attributes` → inserts `model_config = {"from_attributes": True}` | ❌ | run_deterministic_patches — overlapping end-state with the next row (both produce `from_attributes`), different triggers, not flagged duplicate by either docstring |
| `_patch_pydantic_orm_mode` | 2960 | `content: str` → `str` | content has `"orm_mode"` → rewrites v1 `orm_mode=True` (nested `Config` class or bare) to v2 `model_config` | ❌ | inline chain |
| `_patch_star_dict_extra_fields` | 3001 | `project_path: Path` → `int` | route does `Model(**schema.dict())`/`.model_dump()` for a known SQLAlchemy class → rewrites to a dict comprehension filtered against `Model.__table__.columns.keys()` (deliberately not `hasattr`), excludes explicit trailing kwargs from the filter | ❌ | run_deterministic_patches — first of a 3-function lineage maintaining one invariant, see next two rows |
| `_patch_filtered_ctor_kwarg_collision` | 3084 | `project_path: Path` → `int` | content already has the `__table__.columns.keys()` shape but is missing the trailing-kwarg exclusion clause → adds it | ❌ | run_deterministic_patches — narrow version-skew backstop for the row above |
| `_patch_unsafe_model_hasattr_filter` | 3137 | `project_path: Path` → `int` | content has `hasattr(Model, k)` used as a filter → rewrites to `k in Model.__table__.columns.keys()`, retroactively applying the fix `_patch_star_dict_extra_fields` produces going forward | ❌ | run_deterministic_patches |
| `_patch_attr_access_mismatches` | 3199 | `project_path: Path` → `int` | a route references `.bad_attr` (fixed synonym table) not a real column on the referenced class → whole-file regex substitution (not class-qualified); deliberately excludes ownership-FK fields, see next row | ❌ | run_deterministic_patches |
| `_model_fk_columns` (helper) | 3380 | `models_dir: Path` → `dict` | regex requiring `Column(...ForeignKey...)` specifically | ❌ | called only from the row below |
| `_patch_ownership_fk_attribute_drift` | 3281 | `project_path: Path` → `int` | Exp046: `ClassName.bad_ownership_attr` (fixed synonym table) not a real FK-typed column → class-QUALIFIED substitution (deliberately narrower than the row above, to avoid damaging a different model's correct same-named column) | ✅ `test_ownership_fk_drift.py` | run_deterministic_patches |
| `_patch_missing_create_update_fields` | 3442 | `project_path: Path` → `int` | **uses `ast.parse`/`ast.walk`.** a route param typed `XCreate`/`XUpdate` accesses `x_in.field` not declared on that schema, corroborated by a sibling schema or the model's own columns → per-function-scoped AST walk (not file-wide, to avoid cross-contamination), always adds `Optional[Any] = None` | ✅ `test_missing_create_update_fields.py` (+2 more) | run_deterministic_patches |
| `_patch_missing_pydantic_imports` | 3598 | `project_path: Path` → `int` | a schema file uses a pydantic/typing/stdlib symbol without importing or defining it → regex usage+import detection, merges or injects import lines | ❌ | run_deterministic_patches |
| `_patch_orm_type_in_route_schemas` | 3716 | `project_path: Path` → `int` | a Pydantic class defined in a route file has a field type referencing a SQLAlchemy ORM class (bare/`List[X]`/`Optional[X]`) → 3 regex passes rewrite to `Any` | ❌ | run_deterministic_patches |
| `_patch_list_response_model_mismatch` | 3796 | `project_path: Path` → `int` | a route decorated `response_model=List[...]` whose body returns an `{"items":...}` shape → strips the `response_model=List[...]` clause | ❌ | run_deterministic_patches |
| `_is_self_referential_import` (helper) | 3907 | `text, module_name` → `bool` | matches `import {X} from 'X'` self-reference shape | ❌ | called only from the row below |
| `_patch_frontend_package_json` | 3938 | `project_path: Path` → `bool` | a `.jsx`/`.js`/`.tsx` file imports a bare module name not in package.json → filters out invalid npm names and self-references, expands to fixed peer-dependency table, re-pins specific packages away from `"latest"` for known API-shape drift | ❌ | run_frontend_patches |
| `_infer_crud_func` (helper) | 4029 | `func, model_cls, resource, pk` → `str` | regex-classifies the function name against CRUD naming conventions, generates a matching stub body | ❌ | called only from the row below |
| `_patch_create_missing_service_stubs` | 4073 | `project_path: Path` → `int` | a route does `from app.services.X import func_name` where X.py doesn't define it → builds a resource→model map (supporting both classic and SQLAlchemy 2.0 `Mapped[...]` styles), generates CRUD stubs | ❌ | run_deterministic_patches |
| `_patch_missing_db_refresh` | 4174 | `project_path: Path` → `int` | `db.add(x)` immediately followed by `db.commit()` with no `db.refresh(x)` in the next 3 lines (POST would return `id=None`) → injects it | ❌ | run_deterministic_patches |
| `_patch_wire_orphan_routers` | 4223 | `project_path: Path` → `None` | a route file defines `X_router = APIRouter(...)` not imported+included in main.py → injects whichever is missing | ❌ | run_deterministic_patches |
| `_patch_router_export_mismatch` | 4325 | `project_path: Path` → `int` | a route file's sole `APIRouter()` assignment isn't the exact expected `{stem}_router` name → appends a one-line alias (lower-risk than `_patch_router_names`'s rename); skips files with 0 or 2+ router assignments | ❌ | **not called anywhere in `deterministic_patcher.py`** — called directly from `services/v6_orchestrator.py:441,1086` (confirmed live, see `REPAIR_GRAPH.md` §6) |
| `patch_ensure_auth_pages` | 4585 | `project_path: Path` → `int` | App.jsx references `/login` and LoginPage.jsx doesn't exist (RegisterPage synthesized too if `/register` referenced) → full-file write of hardcoded known-good templates, injects the import + `<Route>`; deliberately does NOT reuse `_patch_wire_orphan_frontend_routes` (would wrap the page in the auth-guard it exists to satisfy) | ❌ | run_deterministic_patches **and** run_frontend_patches — confirmed runs twice per generation (idempotent, checks `login_path.exists()` first), see `REPAIR_DEBT.md` Risk 6 |
| `_word_synonyms`/`_component_words`/`_best_matching_path` (helpers) | 4680/4687/4693 | various → `str \| None` | CRUD-verb-synonym-aware fuzzy matching between a component name and candidate route paths | ❌ | called only from the row below |
| `_patch_dedupe_frontend_imports` | 4731 | `project_path: Path` → `None` | a `.jsx` file has the same default-import identifier on 2+ lines → drops re-declarations after the first | ❌ | run_deterministic_patches |
| `_patch_wire_orphan_frontend_routes` | 4771 | `project_path: Path` → `None` | a page component is imported (or exists unimported) but never rendered on a `<Route>` → auto-imports, fuzzy-matches a URL path, clones an existing authenticated route's element wrapper via regex, falls back to kebab-case or bare `PrivateRoute` | ❌ | run_deterministic_patches |
| `_patch_login_redirect_target` | 4953 | `project_path: Path` → `int` | no `<Route path="/dashboard">` but something still hardcodes a `/dashboard` reference → rewrites the 3 hardcoded-reference shapes to a fallback target | ❌ | run_deterministic_patches — explicitly documented as depending on `_patch_wire_orphan_frontend_routes` having already run; call order places it immediately after |
| `_patch_schema_nullable_required_mismatch` | 5020 | `project_path: Path` → `int` | **uses `ast.parse`/`ast.walk`.** a required schema field's matching model column has `nullable=True` → AST-detects, source-segment-splices the rewrite (skips multi-line annotations as too risky to rewrite textually) | ❌ | run_deterministic_patches — imports `_is_optional_annotation` from `app.services.schema_model_validator` (only cross-module coupling found in this file) |
| `_patch_response_schema_id_and_datetimes` | 5135 | `project_path: Path` → `int` | **uses `ast.parse`/`ast.walk`.** a response-ish schema is missing an `id` field its model has, or has a wrong str/date/datetime/time family annotation vs. the model's real column type → AST-based detect + insert/retype | ❌ | run_deterministic_patches — imports `_collect_response_model_schemas` from the same external module as the row above |
| `_heroicon_to_lucide` (helper) | 5422 | `name: str` → `str` | fixed override table, then strip-suffix-and-membership-check against a curated+validated icon set, falls back to `"Circle"` (always exists) | ❌ | called only from the row below |
| `_patch_disallowed_icon_packages` | 5434 | `project_path: Path` → `int` | a `.jsx` imports from `@heroicons/react/...` → strips the import, renames usages via the helper above, merges into a `lucide-react` import | ❌ | run_frontend_patches |
| `_module_dotted`/`_backend_module_exists` (helpers) | 5505/5517 | `Path`-based | pure path↔dotted-module conversion and existence check | ❌ | called only from the row below |
| `_patch_redirect_missing_backend_imports` | 5525 | `project_path: Path` → `int` | a `.py` file does `from app.X import ...` where `app/X.py` doesn't exist → builds a project-wide symbol index, redirects to the module covering ALL imported names, or generates a physical re-export shim as a last resort | ❌ | run_deterministic_patches — must run before router wiring |
| `_patch_hidden_loading_status` | 5654 | `project_path: Path` → `int` | a `.jsx` has both `{status && <p` and `{loading ? (` in a specific nested shape → hoists the status message out of the loading ternary | ❌ | run_frontend_patches |
| `_patch_frontend_auth_field_names` | 5706 | `project_path: Path` → `int` | a `.jsx` referencing `/auth/login`/`/auth/register` has the specific `.username`/`.id` guard+`localStorage.setItem` pair → rewrites to `.display_name`/`.user_id` within the matched pair only | ❌ | run_frontend_patches |
| `_patch_frontend_signup_password_key` | 5753 | `project_path: Path` → `int` | a `.jsx` referencing `/auth/register`/`/auth/signup` has literal `"hashed_password"` → rewrites to `password:` | ❌ | run_frontend_patches |
| `_patch_stale_status_on_error` | 5797 | `project_path: Path` → `int` | a `.jsx` declares `setStatus(` with the specific error-branch idiom missing a `setStatus(null)` clear → inserts it | ❌ | run_frontend_patches |
| `_patch_invalid_lucide_icons` | 5855 | `project_path: Path` → `int` | a `lucide-react` import includes a name not in the pinned-version-validated set → rewrites via an override table (fallback `Circle`), renames usages | ✅ `test_icon_validity.py` | run_frontend_patches — complementary pair with the next row (wrong name already imported vs. used-but-never-imported) |
| `_patch_missing_icon_imports` | 5941 | `project_path: Path` → `int` | a PascalCase JSX tag is neither imported nor locally defined and is a known icon or router component name → injects the missing import | ❌ | run_frontend_patches |
| `_patch_unsafe_optional_chain_before_array_method` | 6043 | `project_path: Path` → `int` | `x?.y.map(...)`/`.length` — optional-chained on the base but not the nested access → inserts a second `?.` | ❌ | run_frontend_patches |
| `_patch_response_data_used_as_bare_array` | 6088 | `project_path: Path` → `int` | `res.data.map(...)` assuming a bare-array response → wraps in an `Array.isArray(...)` fallback chain | ❌ | run_frontend_patches — inverse-direction complementary pair with the next row (same root cause: backend/frontend generated independently with no shared response-shape contract) |
| `_patch_response_data_assumed_wrapped` | 6141 | `project_path: Path` → `int` | `res.data.items`/`.entries`/`.results` assuming a wrapped response → wraps in the inverse fallback chain | ❌ | run_frontend_patches |

**Verified in this enrichment pass, not previously stated:** only 4 of the
66 functions use Python's real `ast` module (`_infer_fields_from_route_return`,
`_patch_missing_create_update_fields`, `_patch_schema_nullable_required_mismatch`,
`_patch_response_schema_id_and_datetimes`) — every other function is
regex/string-based. One cosmetic type-hint inconsistency:
`patch_reorder_shadowed_static_routes` takes `project_path: str` where
every sibling takes `Path` (not a functional bug — the function converts
internally).

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
