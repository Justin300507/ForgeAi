import os
import re
import py_compile
import ast
from app.utils.brace_matching import find_matching_brace
from app.services.undefined_symbol_validator import (
    validate_undefined_symbols
)
from app.services.architecture_validator import (
    validate_architecture,
    load_metadata
)
from app.services.orm_validator import (
    validate_orm_usage,
    validate_no_flask_sqlalchemy
)
from app.services.endpoint_validator import (
    validate_endpoints,
    validate_orphan_routes,
    validate_frontend_api_calls
)
from app.services.database_validator import (
    validate_database
)
from app.services.router_export_validator import (
    validate_router_exports
)
from app.services.schema_model_validator import (
    validate_schema_model_consistency
)
from app.services.session_validator import (
    validate_session_management
)
from app.services.self_shadow_validator import (
    validate_self_shadowing_functions
)
from app.services.stub_handler_validator import validate_stub_handlers
from app.services.global_statement_validator import validate_module_level_global
from app.services.duplicate_class_validator import validate_duplicate_class_definitions


def rel(project_path, file_path):
    """Canonical relative path with forward slashes, used by every
    validator below so error messages always reference files the
    same way (no more app\\routes\\x.py vs routes/x.py vs x.py)."""
    return os.path.relpath(file_path, project_path).replace(os.sep, "/")


def validate_backend_imports(project_path, errors):
    _SKIP_DIRS = {"node_modules", ".git", "__pycache__", ".venv", "venv", "dist"}
    for root, dirs, files in os.walk(project_path):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]

        for file in files:

            if not file.endswith(".py"):
                continue

            file_path = os.path.join(
                root,
                file
            )

            try:

                with open(
                    file_path,
                    "r",
                    encoding="utf-8"
                ) as f:

                    content = f.read()

            except Exception:
                continue

            # Generalized over any depth ("app.config", "app.services.foo",
            # "app.services.foo.bar") instead of a fixed folder whitelist — a
            # single-segment top-level import like "from app.config import
            # settings" previously matched no whitelisted folder and silently
            # bypassed every static check, only surfacing as a runtime
            # ModuleNotFoundError.
            imports = re.findall(
                r"^from\s+app\.([\w.]+)\s+import",
                content,
                re.MULTILINE,
            )

            for dotted in imports:

                segments = dotted.split(".")

                expected = os.path.join(
                    project_path,
                    "app",
                    *segments[:-1],
                    f"{segments[-1]}.py"
                )

                if os.path.exists(expected):
                    continue

                package_init = os.path.join(
                    project_path, "app", *segments, "__init__.py"
                )

                if os.path.exists(package_init):
                    continue

                errors.append(
    f"Missing backend import target: app/{'/'.join(segments)}.py"
)

def validate_imported_symbols(
    project_path,
    errors
):
    _SKIP_DIRS = {"node_modules", ".git", "__pycache__", ".venv", "venv", "dist"}
    for root, dirs, files in os.walk(project_path):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]

        for file in files:

            if not file.endswith(".py"):
                continue

            file_path = os.path.join(
                root,
                file
            )

            try:

                with open(
                    file_path,
                    "r",
                    encoding="utf-8"
                ) as f:

                    content = f.read()

                tree = ast.parse(
                    content
                )

            except Exception:
                continue

            for node in ast.walk(tree):

                if not isinstance(
                    node,
                    ast.ImportFrom
                ):
                    continue

                if not node.module:
                    continue

                if not node.module.startswith(
                    "app."
                ):
                    continue

                module_path = (
                    node.module.replace(
                        ".",
                        os.sep
                    )
                    + ".py"
                )

                target_file = os.path.join(
                    project_path,
                    module_path
                )

                is_package = False
                package_dir = None

                if not os.path.exists(target_file):

                    package_dir = os.path.join(
                        project_path,
                        node.module.replace(".", os.sep)
                    )
                    package_init = os.path.join(package_dir, "__init__.py")

                    if os.path.exists(package_init):
                        target_file = package_init
                        is_package = True
                    else:
                        continue

                try:

                    with open(
                        target_file,
                        "r",
                        encoding="utf-8"
                    ) as f:

                        target_content = f.read()

                    target_tree = ast.parse(
                        target_content
                    )

                except Exception:
                    continue

                symbols = set()

                for item in target_tree.body:

                    if isinstance(
                        item,
                        ast.FunctionDef
                    ):
                        symbols.add(
                            item.name
                        )

                    elif isinstance(
                        item,
                        ast.AsyncFunctionDef
                    ):
                        symbols.add(
                            item.name
                        )

                    elif isinstance(
                        item,
                        ast.ClassDef
                    ):
                        symbols.add(
                            item.name
                        )

                    elif isinstance(
                        item,
                        ast.Assign
                    ):

                        for target in item.targets:

                            if isinstance(
                                target,
                                ast.Name
                            ):
                                symbols.add(
                                    target.id
                                )

                    elif isinstance(item, ast.ImportFrom):

                        for alias in item.names:
                            symbols.add(alias.asname or alias.name)

                    elif isinstance(item, ast.Import):

                        for alias in item.names:
                            symbols.add(
                                alias.asname or alias.name.split(".")[0]
                            )

                for alias in node.names:

                    if alias.name == "*":
                        continue

                    if alias.name in symbols:
                        continue

                    if is_package:
                        submodule_path = os.path.join(
                            package_dir, f"{alias.name}.py"
                        )
                        if os.path.exists(submodule_path):
                            continue

                    errors.append(
                        f"Missing symbol '{alias.name}' in {rel(project_path, target_file)} "
                        f"(imported from {rel(project_path, file_path)})"
                    )


def validate_frontend_imports(project_path, errors):

    src_path = os.path.join(
        project_path,
        "src"
    )

    if not os.path.exists(src_path):
        return

    for root, dirs, files in os.walk(src_path):

        for file in files:

            if not file.endswith(".jsx"):
                continue

            file_path = os.path.join(
                root,
                file
            )

            try:

                with open(
                    file_path,
                    "r",
                    encoding="utf-8"
                ) as f:

                    content = f.read()

            except Exception:
                continue

            imports = re.findall(
                r'import\s+.*?\s+from\s+[\'"](.+?)[\'"]',
                content
            )

            for imp in imports:

                if not imp.startswith("."):
                    continue

                possible_paths = []

                if imp.endswith(".jsx"):

                    possible_paths.append(
                        os.path.normpath(
                            os.path.join(
                                root,
                                imp
                            )
                        )
                    )

                elif imp.endswith(".js"):

                    possible_paths.append(
                        os.path.normpath(
                            os.path.join(
                                root,
                                imp
                            )
                        )
                    )

                else:

                    possible_paths.append(
                        os.path.normpath(
                            os.path.join(
                                root,
                                imp + ".jsx"
                            )
                        )
                    )
                    possible_paths.append(
                        os.path.normpath(
                            os.path.join(
                                root,
                                imp + ".js"
                            )
                        )
                    )

                filename = os.path.basename(
                    imp.replace(".jsx", "").replace(".js", "")
                )

                # An import like "./contexts/AuthContext" specifies an exact
                # subdirectory — a real bundler resolves it literally, with no
                # fuzzy fallback. Only fall back to guessing pages/components/
                # utils/src-root locations for flat imports (e.g. "./AuthContext")
                # where the LLM may have put the file in a conventional folder
                # without matching the import path exactly.
                _imp_rel = imp
                while _imp_rel.startswith("../") or _imp_rel.startswith("./"):
                    _imp_rel = _imp_rel[3:] if _imp_rel.startswith("../") else _imp_rel[2:]
                has_explicit_subdir = "/" in _imp_rel

                if not has_explicit_subdir:

                    possible_paths.append(
                        os.path.join(
                            src_path,
                            "pages",
                            f"{filename}.jsx"
                        )
                    )

                    possible_paths.append(
                        os.path.join(
                            src_path,
                            "components",
                            f"{filename}.jsx"
                        )
                    )

                    possible_paths.append(
                        os.path.join(
                            src_path,
                            filename,
                            "index.jsx"
                        )
                    )

                    # Check src/utils/ — JS utilities provided by templates
                    possible_paths.append(
                        os.path.join(src_path, "utils", f"{filename}.js")
                    )
                    possible_paths.append(
                        os.path.join(src_path, "utils", f"{filename}.jsx")
                    )
                    # Check src-root for files generated at the top level
                    possible_paths.append(os.path.join(src_path, f"{filename}.jsx"))
                    possible_paths.append(os.path.join(src_path, f"{filename}.js"))

                exists = any(
                    os.path.exists(path)
                    for path in possible_paths
                )

                if not exists:

                    errors.append(
                        f"Missing frontend import target: {imp}"
                    )

                else:

                    resolved_path = next(
                        (p for p in possible_paths if os.path.exists(p)),
                        None
                    )

                    if resolved_path:

                        try:
                            with open(
                                resolved_path, "r", encoding="utf-8"
                            ) as tf:
                                target_content = tf.read()
                        except Exception:
                            target_content = ""

                        has_default_export = "export default" in target_content

                        specific_import_match = re.search(
                            r'import\s+(\{[^}]*\}|\S+)\s+from\s+[\'"]'
                            + re.escape(imp) + r'[\'"]',
                            content
                        )

                        is_named_import_for_this_target = bool(
                            specific_import_match
                            and specific_import_match.group(1).startswith("{")
                        )

                        if has_default_export and is_named_import_for_this_target:

                            errors.append(
                                f"Import style mismatch: {imp} uses a default "
                                f"export but is imported with named-import "
                                f"syntax (curly braces) in "
                                f"{os.path.relpath(file_path, project_path)}"
                            )


_NAV_TARGET_RE = re.compile(
    r'(?:to|href)="(/[a-zA-Z][a-zA-Z0-9/_-]*)"|navigate\(\s*[\'"](/[a-zA-Z][a-zA-Z0-9/_-]*)[\'"]'
)
_SKIP_NAV_TARGETS = {"/", "/login", "/register", "/dashboard", "/logout"}


def _page_name_for_path(path):
    """/weekly-report -> WeeklyReportPage (mirrors frontend_scaffold_service's
    reverse conversion, so a synthesized page lands where ensure_app_jsx and
    the orphan-route patcher both expect to find it)."""
    stem = path.rstrip("/").split("/")[-1]
    words = re.split(r"[-_]", stem)
    return "".join(w.capitalize() for w in words if w) + "Page"


# Verb synonyms so a route segment and a page-name word don't have to be
# spelled identically to count as the same CRUD intent (mirrors the matching
# used by deterministic_patcher's orphan-route wirer — kept in sync so a page
# this validator considers "already served" is the same one that patcher
# would actually wire up).
_NAV_CRUD_VERB_SYNONYMS = [
    frozenset({"new", "add", "create"}),
    frozenset({"edit", "update"}),
    frozenset({"delete", "remove"}),
    frozenset({"detail", "details", "view", "show"}),
]


def _nav_word_synonyms(word):
    for group in _NAV_CRUD_VERB_SYNONYMS:
        if word in group:
            return group
    return frozenset({word})


def _path_matches_existing_page(target, existing_pages):
    """True if some already-on-disk page plausibly serves this route.

    Comparing only the route's last segment against each page's filename
    (the old approach) misses exactly the routes that matter most: create/edit
    forms whose distinguishing noun sits in an earlier segment while the
    component is named things like AddEditHabitPage — "/habits/new" vs.
    "addedithabitpage" never shared a last-segment substring, so a page that
    already existed and already handled this route was reported as missing,
    and a generic placeholder page got generated (and wired) in its place.
    """
    segs = [s for s in target.strip("/").split("/") if s and not s.startswith(":") and not s.startswith("{")]
    if not segs:
        return False

    for page in existing_pages:
        page_words = {w.lower() for w in re.findall(r"[A-Z][a-z0-9]*", page) if w.lower() != "page"}
        if not page_words:
            page_words = {page.lower().replace("page", "")}

        score = 0
        for seg in segs:
            seg_l = seg.lower().replace("-", "").replace("_", "")
            seg_singular = seg_l[:-1] if seg_l.endswith("s") and len(seg_l) > 3 else seg_l
            candidates = _nav_word_synonyms(seg_l) | _nav_word_synonyms(seg_singular) | {seg_l, seg_singular}
            if candidates & page_words:
                score += 1
            elif any(len(c) > 2 and len(pw) > 2 and (c in pw or pw in c) for c in candidates for pw in page_words):
                score += 1
        if score > 0:
            return True

    return False


def validate_frontend_nav_targets(project_path, errors):
    """
    A page can be truncated out of the LLM's frontend response (hits its
    token limit after Dashboard/Login/Register, a big page or two, and never
    gets to the rest) while the pages that DID generate still link to it —
    Header/Sidebar nav items pointing at /habits, /badges, /settings etc.
    with no corresponding page file anywhere. validate_frontend_imports only
    catches missing `import` targets; a <Link to="/badges"> or
    navigate('/badges') isn't an import at all, so a page silently missing
    like this produces zero validation errors and the dead link only shows
    up as "click Badges, land back on Dashboard" in the deployed app.

    Emits errors in the exact "Missing frontend import target: X" format
    validate_frontend_imports already uses so this flows through the
    existing missing-file fix dispatch (see v6_orchestrator.py's regex on
    that exact string) without needing new wiring — the fix loop already
    knows how to turn that message into a real generate_missing_file() call.
    """
    src_path = os.path.join(project_path, "src")
    if not os.path.exists(src_path):
        return

    app_jsx = os.path.join(src_path, "App.jsx")
    routed_paths = set()
    if os.path.exists(app_jsx):
        try:
            with open(app_jsx, "r", encoding="utf-8") as f:
                routed_paths = set(re.findall(r'<Route\s+path="([^"]+)"', f.read()))
        except Exception:
            pass

    pages_dir = os.path.join(src_path, "pages")
    existing_pages = set()
    if os.path.isdir(pages_dir):
        existing_pages = {
            os.path.splitext(f)[0] for f in os.listdir(pages_dir)
            if f.endswith((".jsx", ".js"))
        }

    nav_targets = set()
    for root, _dirs, files in os.walk(src_path):
        for file in files:
            if not file.endswith(".jsx"):
                continue
            try:
                with open(os.path.join(root, file), "r", encoding="utf-8") as f:
                    content = f.read()
            except Exception:
                continue
            for m in _NAV_TARGET_RE.finditer(content):
                target = m.group(1) or m.group(2)
                if target:
                    nav_targets.add(target)

    reported = set()
    for target in sorted(nav_targets - _SKIP_NAV_TARGETS - routed_paths):
        if _path_matches_existing_page(target, existing_pages):
            continue  # a page that plausibly serves this route already exists on disk
        page_name = _page_name_for_path(target)
        if page_name in reported:
            continue
        reported.add(page_name)
        errors.append(f"Missing frontend import target: ./pages/{page_name}")


def validate_frontend_api_client(project_path, errors):
    """
    Flag raw fetch() calls with relative URLs in src/ files.

    Relative fetch('/todos') works in local dev (Vite proxies to the backend)
    but 404s in production because the frontend (Cloudflare Pages) and backend
    (Render) live on different domains. Every HTTP call must go through the
    shared axios client in src/api.js, which reads VITE_API_URL and attaches
    the auth header. This bug shipped a fully-blank production app (simple_todo,
    2026-07-02): 7 repair-generated pages all used raw fetch().
    """
    src_path = os.path.join(project_path, "src")
    if not os.path.exists(src_path):
        return

    _SKIP_DIRS = {"node_modules", "dist", ".git"}
    for root, dirs, files in os.walk(src_path):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for file in files:
            if not file.endswith((".jsx", ".js")):
                continue
            if file == "api.js":
                continue  # the shared client itself may use whatever it wants
            file_path = os.path.join(root, file)
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read()
            except Exception:
                continue

            # fetch("/x"...), fetch(`/x/${id}`...) — relative URL as the first arg
            raw_fetches = re.findall(
                r"""fetch\(\s*(?:["']/|`/)""",
                content,
            )
            if raw_fetches:
                errors.append(
                    f"Frontend raw fetch: {rel(project_path, file_path)} makes "
                    f"{len(raw_fetches)} HTTP call(s) via fetch() with a relative "
                    f"URL — must use the shared axios client instead "
                    f"(import API from '../api'; API.get('/todos')). Relative "
                    f"fetch() 404s in production because the frontend and "
                    f"backend are deployed on different domains."
                )


_AUTH_POST_RE = re.compile(
    r"""\.post\(\s*[`'"][^`'"]*(?:register|signup|login|signin|sign-in)[^`'"]*[`'"]\s*,\s*(\{)""",
    re.IGNORECASE,
)


def _extract_object_literal(content, open_brace_pos):
    """
    From the position of an opening '{', return the full literal span up to
    its matching '}', tracking string literals (single/double/backtick --
    JS/JSX object literals can use any of the three) so braces inside
    strings don't throw off the depth count. Returns None if unbalanced.

    Exp053: thin wrapper over the shared app.utils.brace_matching
    implementation (this function's own algorithm, extracted -- verified
    byte-identical behavior before and after against a set of real and
    representative object-literal fixtures, including a mixed-quote case
    that a naive "any of quote_chars toggles in_string" simplification
    would have broken).
    """
    end = find_matching_brace(content, open_brace_pos, quote_chars="'\"`")
    return content[open_brace_pos:end + 1] if end != -1 else None


def _top_level_object_keys(literal):
    """
    Parse an object-literal string (including outer braces) into its
    top-level key names, handling both `key: value` and ES6 shorthand
    `{ key }` forms -- shorthand is exactly what the frontend prompt tells
    the generator to write (`{ email, password, display_name: displayName }`),
    so treating only `key: value` as a "key" would flag every correctly
    generated call as missing its fields.
    """
    inner = literal.strip()
    if inner.startswith("{"):
        inner = inner[1:]
    if inner.endswith("}"):
        inner = inner[:-1]

    segments = []
    depth = 0
    in_str = None
    start = 0
    i = 0
    n = len(inner)
    while i < n:
        ch = inner[i]
        if in_str:
            if ch == "\\":
                i += 2
                continue
            if ch == in_str:
                in_str = None
        elif ch in ("'", '"', "`"):
            in_str = ch
        elif ch in "{[(":
            depth += 1
        elif ch in "}])":
            depth -= 1
        elif ch == "," and depth == 0:
            segments.append(inner[start:i])
            start = i + 1
        i += 1
    segments.append(inner[start:])

    keys = set()
    for seg in segments:
        seg = seg.strip()
        if not seg or seg.startswith("..."):
            continue
        colon = seg.find(":")
        key_part = seg[:colon] if colon != -1 else seg
        key_part = key_part.strip().strip("'\"")
        if key_part.isidentifier():
            keys.add(key_part)
    return keys


def validate_frontend_auth_fields(project_path, errors):
    """
    Flag a Login/Register/Signup form that POSTs a payload missing the
    'email' field the backend requires.

    Every generated backend's auth contract (shared_contract.py + the
    known-good injected app/routes/auth_routes.py) uses `email` as the
    mandatory identity field for POST /auth/register|signup AND
    /auth/login, no matter what the frontend generator produced. When the
    LLM instead builds the form around `username` (a real, observed
    deviation), the request 422s with "email: field required" on every
    single submission — a live-breaking bug that no runtime check catches,
    because the journey runner builds its request body dynamically from
    the OpenAPI schema instead of reading the actual frontend form.

    Originally this only checked register/signup calls. Seen live: a
    LoginPage.jsx built entirely around a "Username" field (state var
    `username`, POSTing `{ username, password }`) passed every automated
    check — the backend's own /auth/login endpoint works fine when called
    correctly, so the "Check & Fix deployed app" tool's direct API test
    and the journey runner (which never reads the frontend form) both
    reported success — while the actual UI 422'd on every single login
    attempt with a "Field required" error shown even with both fields
    correctly filled in, since the frontend never sent `email` at all.
    Extended the endpoint-name match to include login/signin/sign-in so
    this exact failure mode is caught before generation completes, not
    just for registration.
    """
    auth_routes = os.path.join(project_path, "app", "routes", "auth_routes.py")
    if not os.path.exists(auth_routes):
        return
    try:
        with open(auth_routes, "r", encoding="utf-8") as f:
            backend_uses_email = "email" in f.read()
    except Exception:
        return
    if not backend_uses_email:
        return  # this project's auth contract isn't email-based — don't assume

    src_path = os.path.join(project_path, "src")
    if not os.path.exists(src_path):
        return

    _SKIP_DIRS = {"node_modules", "dist", ".git"}
    for root, dirs, files in os.walk(src_path):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for file in files:
            if not file.endswith((".jsx", ".js", ".tsx", ".ts")):
                continue
            file_path = os.path.join(root, file)
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read()
            except Exception:
                continue

            for m in _AUTH_POST_RE.finditer(content):
                literal = _extract_object_literal(content, m.start(1))
                if literal is None:
                    continue
                if "..." in literal:
                    continue  # spread payload — can't statically verify, don't guess
                keys = _top_level_object_keys(literal)
                if not keys or "email" in keys:
                    continue
                is_login = bool(re.search(r"login|signin|sign-in", m.group(0), re.IGNORECASE))
                if is_login:
                    errors.append(
                        f"Frontend auth field mismatch: {rel(project_path, file_path)} "
                        f"POSTs to a login endpoint with fields {sorted(keys)} but no "
                        f"'email' — the backend's login schema requires email (per "
                        f"project contract), so every login attempt will fail with a "
                        f"422 even though the form is filled in correctly (the UI will "
                        f"show a confusing generic 'Field required' error). Rename the "
                        f"field/state var to email and send {{ email, password }} in "
                        f"the POST body."
                    )
                else:
                    errors.append(
                        f"Frontend auth field mismatch: {rel(project_path, file_path)} "
                        f"POSTs to a register/signup endpoint with fields "
                        f"{sorted(keys)} but no 'email' — the backend's signup "
                        f"schema requires email (per project contract), so every "
                        f"registration will fail with a 422 and the UI will hang "
                        f"on its loading state. Add an email input and send "
                        f"{{ email, password, display_name }} in the POST body."
                    )


def validate_route_quality(
    project_path,
    errors
):

    routes_dir = os.path.join(
        project_path,
        "app",
        "routes"
    )

    if not os.path.exists(
        routes_dir
    ):
        return

    for file in os.listdir(
        routes_dir
    ):

        if not file.endswith(".py"):
            continue

        if file == "__init__.py":
            continue

        file_path = os.path.join(
            routes_dir,
            file
        )

        try:

            with open(
                file_path,
                "r",
                encoding="utf-8"
            ) as f:

                content = f.read()

        except Exception:
            continue

        if "APIRouter(" not in content:

            errors.append(
                f"Missing APIRouter in {rel(project_path, file_path)}"
            )

        route_count = len(
            re.findall(
                r"@\w+\.(get|post|put|patch|delete)",
                content
            )
        )

        if route_count == 0:

            errors.append(
                f"No endpoints found in {rel(project_path, file_path)}"
            )


def validate_requirements(
    project_path,
    errors
):

    requirements_file = os.path.join(
        project_path,
        "app",
        "requirements.txt"
    )
    if not os.path.exists(
        requirements_file
    ):

        errors.append(
    "Missing requirements.txt (app/requirements.txt)"
)
        return

    try:

        with open(
            requirements_file,
            "r",
            encoding="utf-8"
        ) as f:

            content = f.read().strip()

        if not content:

            errors.append(
    "requirements.txt is empty (app/requirements.txt)"
)

    except Exception as e:

        errors.append(
            f"Could not read requirements.txt: {str(e)}"
        )


def validate_common_antipatterns(project_path, errors):
    """Catch LLM anti-patterns that validators miss but always crash at runtime."""
    routes_dir = os.path.join(project_path, "app", "routes")
    if not os.path.isdir(routes_dir):
        return

    for fname in os.listdir(routes_dir):
        if not fname.endswith(".py"):
            continue
        fpath = os.path.join(routes_dir, fname)
        try:
            content = open(fpath, encoding="utf-8").read()
        except Exception:
            continue

        rel_path = rel(project_path, fpath)

        # auth2_scheme typo — crashes FastAPI at startup
        if "auth2_scheme" in content and "oauth2_scheme" not in content:
            errors.append(
                f"Typo 'auth2_scheme' in {rel_path} — must be 'oauth2_scheme'. "
                "Define: oauth2_scheme = OAuth2PasswordBearer(tokenUrl='/auth/token')"
            )

        # werkzeug import — Flask lib, not installed
        if "from werkzeug" in content or "import werkzeug" in content:
            errors.append(
                f"werkzeug imported in {rel_path} — Flask library, not available. "
                "Use passlib: from passlib.context import CryptContext"
            )

        # orm_mode — Pydantic v1 deprecated in v2
        if "orm_mode = True" in content:
            errors.append(
                f"orm_mode=True in {rel_path} — use from_attributes=True (Pydantic v2)"
            )

    # Check schema files for datetime fields typed as str (causes ResponseValidationError)
    schemas_dir = os.path.join(project_path, "app", "schemas")
    if os.path.isdir(schemas_dir):
        _DT_FIELDS_RE = re.compile(
            r'^\s+(created_at|updated_at|created|updated|timestamp\w*)\s*:\s*str\b',
            re.MULTILINE
        )
        for fname in os.listdir(schemas_dir):
            if not fname.endswith(".py"):
                continue
            fpath = os.path.join(schemas_dir, fname)
            try:
                content = open(fpath, encoding="utf-8").read()
            except Exception:
                continue
            m = _DT_FIELDS_RE.search(content)
            if m:
                rel_path = rel(project_path, fpath)
                errors.append(
                    f"Schema datetime typed as str in {rel_path}: "
                    f"'{m.group().strip()}' — use 'from datetime import datetime' "
                    "and declare the field as 'datetime', not 'str'. "
                    "Also ensure model_config = ConfigDict(from_attributes=True)."
                )


def validate_project(project_path):

    errors = []

    main_file = os.path.join(
        project_path,
        "app",
        "main.py"
    )

    if not os.path.exists(main_file):

        errors.append(
            "Missing app/main.py"
        )

        return {
            "passed": False,
            "errors": errors
        }

    with open(
        main_file,
        "r",
        encoding="utf-8"
    ) as f:

        content = f.read()

    imports = re.findall(
        r"from\s+app\.routes\.(\w+)\s+import",
        content
    )

    for route_name in imports:

        route_file = os.path.join(
            project_path,
            "app",
            "routes",
            f"{route_name}.py"
        )

        if not os.path.exists(route_file):

            errors.append(
                f"Missing route file: app/routes/{route_name}.py"
            )

    validate_backend_imports(
        project_path,
        errors
    )
    validate_imported_symbols(
        project_path,
        errors
    )
    validate_router_exports(
        project_path,
        errors
    )
    validate_database(
        project_path,
        errors
    )
    metadata = load_metadata(
        project_path
    )
    validate_architecture(
        project_path,
        metadata,
        errors
    )
    validate_endpoints(
        project_path,
        metadata,
        errors
    )
    validate_frontend_api_calls(
        project_path,
        errors
    )
    validate_orphan_routes(
        project_path,
        metadata,
        errors
    )
    validate_undefined_symbols(
        project_path,
        errors
    )
    validate_self_shadowing_functions(
    project_path,
    errors
)
    validate_orm_usage(
        project_path,
        errors
    )
    validate_no_flask_sqlalchemy(
        project_path,
        errors
    )
    validate_session_management(
        project_path,
        errors
    )
    validate_schema_model_consistency(
        project_path,
        errors
    )

    validate_frontend_imports(
        project_path,
        errors
    )
    validate_frontend_nav_targets(
        project_path,
        errors
    )
    validate_frontend_api_client(
        project_path,
        errors
    )
    validate_frontend_auth_fields(
        project_path,
        errors
    )
    validate_stub_handlers(
    project_path,
    errors
)
    validate_module_level_global(
    project_path,
    errors
)
    validate_duplicate_class_definitions(
    project_path,
    errors
)
    validate_route_quality(
        project_path,
        errors
    )
    validate_requirements(
        project_path,
        errors
    )
    validate_common_antipatterns(
        project_path,
        errors
    )

    _SKIP_DIRS = {"node_modules", ".git", "__pycache__", ".venv", "venv", "dist", ".next"}
    for root, dirs, files in os.walk(project_path):
        # Prune dirs in-place to skip non-project directories
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]

        for file in files:

            if not file.endswith(".py"):
                continue

            file_path = os.path.join(
                root,
                file
            )

            try:

                py_compile.compile(
                    file_path,
                    doraise=True
                )

            except Exception as e:

                relative_path = os.path.relpath(
                    file_path,
                    project_path
                )

                errors.append(
                    f"Syntax error in {relative_path}: {str(e)}"
                )

    return {
        "passed": len(errors) == 0,
        "errors": list(dict.fromkeys(errors))
    }