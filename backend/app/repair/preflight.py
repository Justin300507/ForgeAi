"""
Preflight Deterministic Fix Registry

Deterministic fixes that don't need an LLM.  Every fix here saves tokens,
latency, and reduces the chance of LLM hallucination introducing new errors.

Registry pattern: each fix is a small callable that returns True if it changed
anything.  Fixes are attempted in priority order.  The registry can be extended
with @preflight.register() without modifying this file.

Current fixes (no LLM needed):
  1. Missing package in requirements.txt        → add it
  2. Wrong postgres:// URL scheme               → fix database.py
  3. PyJWT / bcrypt not in requirements         → add them
  4. Wrong router name (router vs task_router)  → rename
  5. Parameter ordering error                   → reorder params
  6. Missing .env (template)                    → generate skeleton
  7. Port conflict                              → assign free port
  8. Passlib/python-jose in requirements        → swap for PyJWT/bcrypt
  9. Missing __init__.py in app/               → touch it
 10. Unused passlib / werkzeug imports in code  → strip them
 11. BaseModel used as a Query() param type      → loosen to str/Optional[str]
 12. NOT NULL model column absent from Create schema → relax to nullable=True
 13. Frontend sibling imports never generated       → wire in existing stub scaffolder
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Callable, Optional


# ── Registry ──────────────────────────────────────────────────────────────────

class PreflightRegistry:
    """
    Registry of deterministic fix functions.
    Each fix: (project_path: Path, diagnostics: list[dict]) → bool (changed?)
    """

    def __init__(self):
        self._fixes: list[tuple[int, str, Callable]] = []  # (priority, name, fn)

    def register(self, name: str, priority: int = 50):
        """Decorator: @preflight.register("fix_name", priority=10)"""
        def decorator(fn: Callable) -> Callable:
            self._fixes.append((priority, name, fn))
            self._fixes.sort(key=lambda x: x[0])
            return fn
        return decorator

    def run(self, project_path: Path, diagnostics: list | None = None) -> dict[str, bool]:
        """
        Run all registered fixes against the project.
        Returns {fix_name: changed?} for each fix applied.
        """
        diagnostics = diagnostics or []
        results: dict[str, bool] = {}
        for _, name, fn in self._fixes:
            try:
                changed = fn(project_path, diagnostics)
                results[name] = bool(changed)
                if changed:
                    print(f"  [preflight] {name}: applied")
            except Exception as exc:
                results[name] = False
                print(f"  [preflight] {name}: skipped ({exc})")
        total = sum(1 for v in results.values() if v)
        if total:
            print(f"  [preflight] {total} deterministic fix(es) applied without LLM")
        return results


preflight = PreflightRegistry()


# ── Fix implementations ───────────────────────────────────────────────────────

def _req_path(project_path: Path) -> Path:
    return project_path / "app" / "requirements.txt"


def _read_req(project_path: Path) -> list[str]:
    p = _req_path(project_path)
    return p.read_text(encoding="utf-8").splitlines() if p.exists() else []


def _write_req(project_path: Path, lines: list[str]):
    _req_path(project_path).write_text("\n".join(lines) + "\n", encoding="utf-8")


@preflight.register("fix_pyjwt", priority=10)
def _fix_pyjwt(project_path: Path, diagnostics: list) -> bool:
    """Add PyJWT if any file uses `import jwt` but PyJWT isn't in requirements."""
    # Check if any .py file imports jwt
    uses_jwt = any(
        "import jwt" in f.read_text(encoding="utf-8", errors="replace")
        for f in (project_path / "app").rglob("*.py")
        if f.exists()
    )
    if not uses_jwt:
        return False
    lines = _read_req(project_path)
    if any("pyjwt" in l.lower() or l.strip().lower() == "jwt" for l in lines):
        return False
    lines.append("PyJWT")
    _write_req(project_path, lines)
    return True


@preflight.register("fix_bcrypt", priority=11)
def _fix_bcrypt(project_path: Path, diagnostics: list) -> bool:
    """Add bcrypt if utils/auth.py uses it but it's not in requirements."""
    auth_file = project_path / "app" / "utils" / "auth.py"
    if not auth_file.exists():
        return False
    content = auth_file.read_text(encoding="utf-8", errors="replace")
    if "bcrypt" not in content:
        return False
    lines = _read_req(project_path)
    if any("bcrypt" in l.lower() for l in lines):
        return False
    lines.append("bcrypt")
    _write_req(project_path, lines)
    return True


@preflight.register("swap_passlib", priority=12)
def _swap_passlib(project_path: Path, diagnostics: list) -> bool:
    """Remove passlib / python-jose from requirements (incompatible with bcrypt 4+)."""
    lines = _read_req(project_path)
    new_lines = [
        l for l in lines
        if not re.match(r"passlib|python[-_]jose", l.strip(), re.IGNORECASE)
    ]
    if len(new_lines) == len(lines):
        return False
    # Ensure PyJWT and bcrypt are present
    pkgs = {l.lower().split("==")[0].split(">=")[0].strip() for l in new_lines}
    if "pyjwt" not in pkgs:
        new_lines.append("PyJWT")
    if "bcrypt" not in pkgs:
        new_lines.append("bcrypt")
    _write_req(project_path, new_lines)
    return True


@preflight.register("fix_config_missing_settings_instance", priority=13)
def _fix_config_missing_settings_instance(project_path: Path, diagnostics: list) -> bool:
    """
    Ensure app/config.py exports a module-level `settings` instance if it
    defines a Config/Settings class but never instantiated it under that
    exact name -- app/main.py and other files import `settings` by
    convention. This recurs often enough across generations (seen live,
    costing 1-2 otherwise-avoidable LLM fix calls each time) to fix for free.
    Must run before fix_config_missing_attrs (priority 14), which assumes
    the `settings = Config()` line already exists.
    """
    config_file = project_path / "app" / "config.py"
    if not config_file.exists():
        return False
    content = config_file.read_text(encoding="utf-8", errors="replace")

    if re.search(r'^settings\s*=', content, re.MULTILINE):
        return False  # already has a top-level `settings` name

    m = re.search(r'^class (Config|Settings)\b', content, re.MULTILINE)
    if not m:
        return False  # no class to instantiate

    class_name = m.group(1)
    config_file.write_text(content.rstrip() + f"\n\nsettings = {class_name}()\n", encoding="utf-8")
    return True


_POSTGRES_URL_ALREADY_FIXED_RE = re.compile(
    r'DATABASE_URL\s*=\s*DATABASE_URL\.replace\(\s*["\']postgres://'
)


@preflight.register("fix_postgres_url", priority=15)
def _fix_postgres_url(project_path: Path, diagnostics: list) -> bool:
    """Fix postgres:// → postgresql:// in database.py (SQLAlchemy 1.4+ requirement)."""
    db_file = project_path / "app" / "database.py"
    if not db_file.exists():
        return False
    content = db_file.read_text(encoding="utf-8", errors="replace")
    # Exp052: a working runtime guard/replace already covers this -- must
    # check BEFORE the blind literal-replace below, which otherwise matches
    # the exact quoted string "postgres://" wherever it appears, including
    # as the SOURCE argument of an already-correct `.replace("postgres://",
    # "postgresql://")` call. Rewriting that argument turns the call into a
    # permanent self-no-op (confirmed via real generated_projects/ output,
    # see test_preflight_fixes.py's CONFIRMED_BUG tests) and, since the
    # corrupted guard still contains the substring "postgres://", the
    # function re-fires on every subsequent call and appends another
    # duplicate guard -- unbounded growth across repair-loop iterations.
    if _POSTGRES_URL_ALREADY_FIXED_RE.search(content):
        return False
    if "postgres://" not in content:
        return False
    new_content = content.replace(
        '"postgres://"',
        '"postgresql://"'
    )
    # Handle the runtime replace pattern too
    if "postgres://" in new_content:
        new_content = re.sub(
            r'(DATABASE_URL\s*=\s*DATABASE_URL\.replace\s*\()(["\'])postgres://',
            lambda m: m.group(0).replace("postgres://", "postgresql://"),
            new_content
        )
    # If still present, add the runtime fix
    if "postgres://" in new_content:
        fix_line = '\nif DATABASE_URL.startswith("postgres://"):\n    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)\n'
        new_content = new_content + fix_line
    if new_content != content:
        db_file.write_text(new_content, encoding="utf-8")
        return True
    return False


@preflight.register("fix_config_missing_attrs", priority=14)
def _fix_config_missing_attrs(project_path: Path, diagnostics: list) -> bool:
    """
    Ensure commonly-referenced settings attributes (DATABASE_URL, SECRET_KEY,
    etc.) always exist on the config/settings object, even if the LLM's
    generated Config/Settings class forgot to define one of them. Appends a
    runtime guard after the class body rather than parsing/editing it, so it
    works regardless of whether Config is a plain class, dataclass, or
    pydantic BaseSettings -- and is a no-op if everything is already defined.

    Root cause this addresses: `AttributeError: 'Config' object has no
    attribute 'DATABASE_URL'` recurring across generations because
    app/main.py does `create_engine(settings.DATABASE_URL)` but the
    generated Config class sometimes omits that field.

    Confirmed live (2026-07-06 canary) that the instance-only guard below is
    NOT sufficient on its own -- two compounding, independently-occurring
    bugs:
      1. main.py sometimes does its OWN `settings = Config()` (a second,
         fresh instance of the same class) instead of importing config.py's
         `settings` -- an instance-level guard on the wrong object is
         invisible to it. Fix: also set the default on the CLASS itself
         (inherited by every existing AND future instance) whenever the
         instance was built from a plain class actually defined in this
         file.
      2. The LLM sometimes names the field in the opposite case from what
         consuming code expects (`database_url` defined, `DATABASE_URL`
         read, or vice versa) -- Python attribute names are case-sensitive,
         so only patching the canonical case misses this. Fix: guard-set
         both the canonical and lowercase spelling on the class.
    Class-level patching applies to plain classes AND pydantic
    BaseSettings/BaseModel subclasses alike (confirmed empirically
    2026-07-07: a post-class-body `setattr(cls, name, value)` is invisible
    to pydantic's model-building machinery -- that only intercepts
    assignments made inside the class body -- so it's an ordinary class
    attribute a fresh instance's normal attribute lookup falls through to,
    same as any other class). Only skipped when the RHS isn't a plain
    class actually defined in this file (an imported name or a factory
    function -- "the actual class" can't be located to patch).
    """
    defaults = {
        "DATABASE_URL": 'os.getenv("DATABASE_URL", "sqlite:///./app.db")',
        "SECRET_KEY": 'os.getenv("SECRET_KEY", "dev-secret-key-change-in-production")',
        "ALGORITHM": 'os.getenv("ALGORITHM", "HS256")',
        "ACCESS_TOKEN_EXPIRE_MINUTES": 'int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "10080"))',
    }

    config_file = project_path / "app" / "config.py"

    # config.py referenced but missing entirely → write a known-good one.
    # (The LLM fix loop used to handle this; it sometimes wrote a Config
    # class without DATABASE_URL, crashing startup one stage later.)
    if not config_file.exists():
        referenced = any(
            "from app.config import" in f.read_text(encoding="utf-8", errors="replace")
            or "from app import config" in f.read_text(encoding="utf-8", errors="replace")
            for f in (project_path / "app").rglob("*.py")
        )
        if not referenced:
            return False
        body = "\n".join(f"    {attr} = {expr}" for attr, expr in defaults.items())
        config_file.write_text(
            "import os\n\n\nclass Settings:\n"
            '    PROJECT_NAME = os.getenv("PROJECT_NAME", "Generated App")\n'
            f"{body}\n\n\nsettings = Settings()\nConfig = Settings\n",
            encoding="utf-8",
        )
        return True

    content = config_file.read_text(encoding="utf-8", errors="replace")

    _MARKER = "# Preflight patch: ensure commonly-referenced settings attributes always exist"
    if _MARKER in content:
        return False  # guard already appended on a previous pass

    # The settings/config instance, however it was constructed. Used to
    # require the RHS to look like `SomeConfig(` or `SomeSettings(` -- missed
    # the equally common Flask-style factory-function pattern
    # `settings = get_config()` (lowercase "config" inside a function name
    # doesn't match a literal `Config`/`Settings`), which left main.py's
    # `settings.DATABASE_URL` crash completely unpatched. The actual
    # invariant we need is just "there's a module-level `settings` or
    # `config` name to attach a hasattr guard to" -- how it was built
    # doesn't matter.
    m = re.search(
        r'^(settings|config)\s*(?::\s*[\w\[\], .]+)?\s*=\s*(\w+)\s*\(',
        content, re.MULTILINE,
    )
    if m:
        instance_name = m.group(1)
        class_name: Optional[str] = m.group(2)
        # Only class-patch if that name is a plain class actually defined in
        # this file (not an imported name or a factory function).
        #
        # Previously also skipped pydantic BaseSettings/BaseModel subclasses
        # here, on the theory that "arbitrary class-level setattr isn't
        # guaranteed to surface through instance access the same way" for
        # those. Confirmed empirically (2026-07-07) that this is wrong for
        # a non-field extra attribute: `setattr(PydanticModel, "X", v)`
        # after the class body has already executed is invisible to
        # pydantic's model-building machinery entirely (that only
        # intercepts assignments made INSIDE the class body, processed by
        # the metaclass at class-creation time) -- it's an ordinary Python
        # class attribute, and a fresh instance's normal attribute lookup
        # falls through to it exactly like any other class, since the
        # instance's own __dict__/model_fields never shadow it. This
        # closes the actual observed gap: main.py sometimes builds its OWN
        # `Config()`/`Settings()` instance instead of importing config.py's
        # `settings` (see bug #1 above), and when Config is pydantic-based,
        # that second instance was previously left completely unpatched.
        if not re.search(rf'^class\s+{re.escape(class_name)}\b', content, re.MULTILINE):
            class_name = None
    else:
        m2 = re.search(
            r'^(settings|config)\s*(?::\s*[\w\[\], .]+)?\s*=\s*\S',
            content, re.MULTILINE,
        )
        if not m2:
            return False
        instance_name = m2.group(1)
        class_name = None

    # Always append the hasattr guard for every default: it is a runtime
    # no-op when the attribute exists, and checking mere TEXT presence of
    # the name (the old behavior) was fooled by comments and module-level
    # variables while the Config CLASS still lacked the attribute.
    guard_lines = []
    if not re.search(r'^import os\b', content, re.MULTILINE):
        guard_lines.append("import os")
    guard_lines.append("\n" + _MARKER)
    for attr, expr in defaults.items():
        guard_lines.append(f'if not hasattr({instance_name}, "{attr}"):')
        guard_lines.append(f'    try:')
        guard_lines.append(f'        {instance_name}.{attr} = {expr}')
        guard_lines.append(f'    except Exception:')
        guard_lines.append(f'        object.__setattr__({instance_name}, "{attr}", {expr})')

        if class_name:
            case_variants = {attr, attr.lower()}
            for variant in case_variants:
                guard_lines.append(f'if not hasattr({class_name}, "{variant}"):')
                guard_lines.append(f'    setattr({class_name}, "{variant}", {expr})')

    config_file.write_text(content.rstrip() + "\n" + "\n".join(guard_lines) + "\n", encoding="utf-8")
    return True


@preflight.register("fix_missing_init", priority=20)
def _fix_missing_init(project_path: Path, diagnostics: list) -> bool:
    """Add missing __init__.py files in app/ subdirectories."""
    changed = False
    for subdir in (project_path / "app").iterdir():
        if subdir.is_dir() and not subdir.name.startswith(("__", ".")):
            init = subdir / "__init__.py"
            if not init.exists():
                init.write_text("", encoding="utf-8")
                changed = True
    return changed


@preflight.register("fix_query_param_basemodel", priority=22)
def _fix_query_param_basemodel(project_path: Path, diagnostics: list) -> bool:
    """
    FastAPI's dependant analysis requires every `Query(...)`-defaulted
    parameter to be a scalar type (str/int/float/bool/Enum) or a list of
    these -- never an arbitrary Pydantic `BaseModel`. When the backend
    generator emits a "status"/"type"-style schema as a bare
    `class Foo(BaseModel): ...` instead of an `Enum`, and then uses it as a
    Query() type, FastAPI raises `AssertionError: Query parameter '<name>'
    must be one of the supported types` at import time -- before a single
    route registers, so the whole app fails to boot. The existing repair
    loop cannot self-heal this: one retry attempt patches unrelated things
    (auth libs, DB wiring), the identical crash recurs, and the loop gives
    up ("failure signature unchanged").

    Root cause confirmed live (2026-07-06 canary, crm/simple_crm):
    `ContactStatus` was generated as `class ContactStatus(BaseModel): id:
    Optional[int] = None` (not an Enum) and used as
    `status: Optional[ContactStatus] = Query(None, ...)` in a route file.

    Fix: find every plain-`BaseModel` class across `app/schemas` and
    `app/models` (never `Enum`/`BaseSettings` -- those are legitimate Query
    types, left untouched), find every route parameter annotated with one
    of those class names and defaulted via `Query(...)`, and loosen just
    that parameter's annotation to `str`/`Optional[str]` so FastAPI accepts
    it and the app boots. Any `{param}.value` access in the same route file
    (the enum-style access pattern the LLM assumed) is rewritten to a
    `getattr(..., "value", ...)` fallback -- a no-op when the value really
    is an Enum with `.value`, and safe when it's now a plain string.
    """
    routes_dir = project_path / "app" / "routes"
    if not routes_dir.exists():
        return False

    basemodel_classes: set[str] = set()
    for base_dir in ("schemas", "models"):
        d = project_path / "app" / base_dir
        if not d.exists():
            continue
        for f in d.rglob("*.py"):
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            for m in re.finditer(r'^class\s+(\w+)\s*\(\s*BaseModel\s*\)\s*:', text, re.MULTILINE):
                basemodel_classes.add(m.group(1))

    if not basemodel_classes:
        return False

    param_pattern = re.compile(
        r'(\b(\w+)\s*:\s*)(Optional\[\s*(\w+)\s*\]|(\w+))(\s*=\s*Query\()'
    )

    changed = False
    for route_file in routes_dir.rglob("*.py"):
        try:
            text = route_file.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        original = text
        bad_params: list[str] = []

        def _replace(match: re.Match) -> str:
            prefix, _param_name, _type_expr, opt_cls, bare_cls, suffix = match.groups()
            cls_name = opt_cls or bare_cls
            if cls_name not in basemodel_classes:
                return match.group(0)
            bad_params.append(match.group(2))
            new_type = "Optional[str]" if opt_cls else "str"
            return f"{prefix}{new_type}{suffix}"

        text = param_pattern.sub(_replace, text)

        for param_name in bad_params:
            text = re.sub(
                rf'\b{re.escape(param_name)}\.value\b',
                f'getattr({param_name}, "value", {param_name})',
                text,
            )

        if text != original:
            route_file.write_text(text, encoding="utf-8")
            changed = True

    return changed


@preflight.register("fix_frontend_missing_imports", priority=23)
def _fix_frontend_missing_imports(project_path: Path, diagnostics: list) -> bool:
    """
    The recurring `Could not resolve "./Navbar"` / `"./Sidebar"` /
    `"./pages/SignupPage"` / etc. class of Vite build failure (blog_cms,
    confirmed recurring across 8 of 9 canary runs this cycle -- a
    different specific missing file each time) is not a one-off generation
    mistake: the frontend generator routinely emits a page/component that
    imports 2-3 sibling components (Layout -> Navbar/Sidebar/Toast, App ->
    SignupPage, etc.) without a hard guarantee every one of them actually
    gets generated in the same batch.

    Root cause classification: **repair engine**, not the frontend
    generator. A comprehensive fix for exactly this already exists --
    `create_missing_stubs()` in `app/services/frontend_fix_service.py`
    walks every `.jsx` file under `src/`, resolves every relative import,
    and stubs anything unresolved -- but it is never called anywhere in
    the live V15 pipeline (confirmed: zero references to it outside its
    own module and callers of the unused `run_frontend_fix_loop`). The
    pipeline instead only has a narrower, *reactive* per-patch scaffolder
    (`_scaffold_missing_local_imports` in `repair/orchestrator.py`) that
    only stubs imports it sees in a file it happens to be patching *this*
    round. Confirmed live (2026-07-06, Experiment 011 canary log,
    `forge_blog_cms`): Layout.jsx's build error surfaced as "Could not
    resolve './Sidebar'", got patched, then the very next build surfaced
    "Could not resolve './Navbar'" (the same file, a sibling import that
    was never checked until its own turn came up) -- a multi-round
    whack-a-mole burning LLM fix attempts and tokens on something a single
    upfront sweep would resolve for free.

    Fix (smallest deterministic patch, no prompt/generation/route changes):
    call the existing, already-implemented `create_missing_stubs()` once
    here, in the deterministic preflight stage that already runs before
    the first verification pass for every project. This does not change
    what that function does -- it only wires already-written code into the
    pipeline at the correct point instead of leaving it dead.
    """
    if not (project_path / "src").exists():
        return False
    try:
        from app.services.frontend_fix_service import create_missing_stubs
    except Exception:
        return False
    try:
        n_stubs = create_missing_stubs(str(project_path))
    except Exception as exc:
        print(f"  [preflight] fix_frontend_missing_imports: skipped ({exc})")
        return False
    return n_stubs > 0


@preflight.register("fix_model_schema_notnull_gap", priority=24)
def _fix_model_schema_notnull_gap(project_path: Path, diagnostics: list) -> bool:
    """
    The SQLAlchemy model-generation wave and the Pydantic schema/route-
    generation wave run as separate, uncoordinated LLM calls for the same
    entity (confirmed live: `[V6] Wave 2 -- Models (4 tables, parallel)`
    runs one Gemini call per table; schemas/routes are generated in a
    later, independent call). Given the identical architect spec, they can
    still disagree on field naming for the same concept -- e.g. the model
    declares a single `nullable=False` `name` column while the Create
    schema only offers `first_name`/`last_name`. Since the route
    conservatively does `Contact(**{k: v for k, v in data.items() if k in
    Contact.__table__.columns.keys()})`, a column the schema never
    populates is silently omitted rather than raising -- and the DB then
    rejects the NULL at `db.commit()` with an uncatchable
    `IntegrityError: NOT NULL constraint failed`, deep enough in the ORM
    that the route's own `except Exception` handler can only turn it into
    a generic 500, and the CRUD-journey runner never captures an
    entity_id. This is a distinct, newly-exposed sub-cause of
    JourneyCRUDFailure (Experiment 011/012), separate from the type-
    mismatch case already handled by e2f8d77.

    Root cause confirmed live (2026-07-06 canary, crm/simple_crm):
    `Contact.name = Column(String(255), nullable=False)` with no default,
    while `ContactCreate` only defines `first_name`/`last_name`/etc. --
    `name` is never in the request, so it inserts as NULL.

    Fix (smallest deterministic patch, no prompt/generation changes): for
    every `{Model}Create` schema found in `app/schemas`, cross-reference
    its declared field names against `{Model}`'s columns in
    `app/models`. Any column that is `nullable=False`, not a primary key,
    not a `ForeignKey` (those are supplied programmatically by the route,
    e.g. `user_id=current_user.id`, never by the client), has no
    `default`/`server_default`, and whose exact name is not a *required*
    field in the Create schema can never be reliably populated through the
    normal create flow -- relax it to `nullable=True` so the app doesn't
    crash. This does not invent a value or touch route/schema/prompt logic;
    it only prevents a guaranteed-unsatisfiable constraint from crashing
    the request.

    Refinement (Experiment 012 -> 013): the original version of this fix
    checked field *presence* in the Create schema, not *requiredness*.
    Live validation showed that's insufficient: a separate, pre-existing
    patcher (`field_patcher` in `deterministic_patcher.py`) runs *after*
    preflight and reactively stubs a missing field into the Create schema
    as `Optional[str] = None` in response to a different diagnosed error
    (a missing-attribute/constructor error, not a NOT NULL concern). That
    stub makes the field "present" without making it required, so the
    client can still omit it and the column still receives NULL --
    reproducing the exact crash this fix exists to prevent
    (`contacts.name`, confirmed recurring in Experiment 012 despite the
    field technically being "in" the schema). A schema field only
    guarantees the DB gets a non-null value if Pydantic itself would 422
    when the client omits it -- i.e. the field has no `Optional[...]`
    annotation and no default. So the check must be "is this column
    covered by a *required* schema field", not merely "does a field with
    this name exist".
    """
    models_dir = project_path / "app" / "models"
    schemas_dir = project_path / "app" / "schemas"
    if not models_dir.exists() or not schemas_dir.exists():
        return False

    # Collect every {Model}Create schema's *required* field names only --
    # a field with an `Optional[...]` annotation or any default value does
    # not guarantee the client supplies a real value, so it cannot be
    # trusted to satisfy a NOT NULL column.
    create_schema_required_fields: dict[str, set[str]] = {}
    for f in schemas_dir.rglob("*.py"):
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for cm in re.finditer(r'^class\s+(\w+)Create\s*\(\s*BaseModel\s*\)\s*:', text, re.MULTILINE):
            model_name = cm.group(1)
            rest = text[cm.end():]
            end_match = re.search(r'^class\s+\w+', rest, re.MULTILINE)
            body = rest[:end_match.start()] if end_match else rest
            required = set()
            for fm in re.finditer(r'^\s{4}(\w+)\s*:\s*([^\n=]*)(=.*)?$', body, re.MULTILINE):
                field_name, annotation, default = fm.group(1), fm.group(2), fm.group(3)
                if 'Optional[' in annotation or default is not None:
                    continue  # has a default or is Optional -- not required
                required.add(field_name)
            create_schema_required_fields.setdefault(model_name, set()).update(required)

    if not create_schema_required_fields:
        return False

    col_pattern = re.compile(r'^(\s+)(\w+)\s*=\s*Column\(([^\n]*)\)\s*$', re.MULTILINE)
    changed = False

    for model_file in models_dir.rglob("*.py"):
        try:
            text = model_file.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        original = text

        class_matches = list(re.finditer(r'^class\s+(\w+)\s*\(\s*Base\s*\)\s*:', text, re.MULTILINE))
        for class_match in reversed(class_matches):
            required_fields = create_schema_required_fields.get(class_match.group(1))
            if required_fields is None:
                continue  # no Create schema for this model -- nothing to compare against

            class_start = class_match.end()
            next_class = re.search(r'^class\s+\w+', text[class_start:], re.MULTILINE)
            class_end = class_start + next_class.start() if next_class else len(text)
            class_body = text[class_start:class_end]

            def _relax(m: re.Match, _required=required_fields) -> str:
                nonlocal changed
                indent, attr_name, args = m.group(1), m.group(2), m.group(3)
                if attr_name in _required:
                    return m.group(0)
                if 'primary_key' in args or 'ForeignKey' in args:
                    return m.group(0)
                if 'nullable=False' not in args:
                    return m.group(0)
                if 'default=' in args or 'server_default=' in args:
                    return m.group(0)
                changed = True
                return f"{indent}{attr_name} = Column({args.replace('nullable=False', 'nullable=True')})"

            new_class_body = col_pattern.sub(_relax, class_body)
            if new_class_body != class_body:
                text = text[:class_start] + new_class_body + text[class_end:]

        if text != original:
            model_file.write_text(text, encoding="utf-8")

    return changed


@preflight.register("fix_router_names", priority=25)
def _fix_router_names(project_path: Path, diagnostics: list) -> bool:
    """Rename bare `router = APIRouter()` to `{resource}_router = APIRouter()`."""
    try:
        from app.services.deterministic_patcher import _patch_router_names
        result = _patch_router_names(project_path)
        return bool(result)
    except Exception:
        return False


@preflight.register("fix_param_order", priority=26)
def _fix_param_order(project_path: Path, diagnostics: list) -> bool:
    """Reorder route params: body params before Path/Query/Depends."""
    try:
        from app.services.deterministic_patcher import _patch_param_order
        result = _patch_param_order(project_path)
        return bool(result)
    except Exception:
        return False


@preflight.register("fix_missing_env", priority=30)
def _fix_missing_env(project_path: Path, diagnostics: list) -> bool:
    """Generate a .env skeleton if missing (uses sensible defaults)."""
    env_file = project_path / ".env"
    if env_file.exists():
        return False
    # Read main.py to find what env vars are used
    main_py = project_path / "app" / "main.py"
    used_vars: list[str] = []
    if main_py.exists():
        text = main_py.read_text(encoding="utf-8", errors="replace")
        used_vars = re.findall(r'os\.getenv\(["\']([A-Z_]+)["\']', text)
    # Also check all py files for os.getenv
    for f in (project_path / "app").rglob("*.py"):
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
            used_vars.extend(re.findall(r'os\.getenv\(["\']([A-Z_]+)["\']', text))
        except Exception:
            pass

    defaults = {
        "DATABASE_URL":  "sqlite:///./app.db",
        "SECRET_KEY":    "dev-secret-key-change-in-production",
        "ALGORITHM":     "HS256",
        "ACCESS_TOKEN_EXPIRE_MINUTES": "10080",
        "DEBUG":         "true",
    }
    lines = ["# Auto-generated .env skeleton by ForgeAI preflight"]
    seen: set[str] = set()
    for var in dict.fromkeys(used_vars):  # deduplicate, preserve order
        if var in seen:
            continue
        seen.add(var)
        default = defaults.get(var, "")
        lines.append(f"{var}={default}")
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


@preflight.register("fix_strip_passlib_imports", priority=35)
def _fix_strip_passlib_imports(project_path: Path, diagnostics: list) -> bool:
    """Remove passlib/werkzeug imports from Python files (incompatible libraries)."""
    changed = False
    passlib_import_re = re.compile(
        r"^(from passlib\S*|import passlib\S*|from werkzeug\.security\S*|import werkzeug\S*)\s*.*$",
        re.MULTILINE,
    )
    for f in (project_path / "app").rglob("*.py"):
        try:
            content = f.read_text(encoding="utf-8", errors="replace")
            new_content = passlib_import_re.sub("", content)
            if new_content != content:
                f.write_text(new_content, encoding="utf-8")
                changed = True
        except Exception:
            pass
    return changed


@preflight.register("fix_cors_missing", priority=40)
def _fix_cors_missing(project_path: Path, diagnostics: list) -> bool:
    """Add CORS middleware to main.py if missing (required for frontend→backend)."""
    main_py = project_path / "app" / "main.py"
    if not main_py.exists():
        return False
    content = main_py.read_text(encoding="utf-8", errors="replace")
    if "CORSMiddleware" in content:
        return False
    cors_block = (
        '\nfrom fastapi.middleware.cors import CORSMiddleware\n'
        'app.add_middleware(\n'
        '    CORSMiddleware,\n'
        '    allow_origins=["*"],\n'
        '    allow_credentials=True,\n'
        '    allow_methods=["*"],\n'
        '    allow_headers=["*"],\n'
        ')\n'
    )
    # Insert after app = FastAPI(...)
    new_content = re.sub(
        r'(app\s*=\s*FastAPI\([^)]*\))',
        r'\1' + cors_block,
        content,
        count=1,
    )
    if new_content != content:
        main_py.write_text(new_content, encoding="utf-8")
        return True
    return False


@preflight.register("fix_missing_health_endpoint", priority=45)
def _fix_missing_health_endpoint(project_path: Path, diagnostics: list) -> bool:
    """Add GET /health endpoint to main.py if missing."""
    main_py = project_path / "app" / "main.py"
    if not main_py.exists():
        return False
    content = main_py.read_text(encoding="utf-8", errors="replace")
    if '"/health"' in content or "'/health'" in content:
        return False
    health_route = '\n@app.get("/health")\ndef health_check():\n    return {"status": "ok"}\n'
    main_py.write_text(content + health_route, encoding="utf-8")
    return True


@preflight.register("fix_database_py", priority=50)
def _fix_database_py(project_path: Path, diagnostics: list) -> bool:
    """Inject known-good database.py if missing or broken."""
    try:
        from app.services.database_patcher import patch_database_py
        return bool(patch_database_py(str(project_path)))
    except Exception:
        return False
