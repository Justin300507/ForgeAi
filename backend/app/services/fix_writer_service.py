import ast
import os
import re

from app.utils.safe_path import resolve_safe_path, PathTraversalError
from app.utils.atomic_write import atomic_write_text

_CREATE_ALL_SNIPPET = "\nfrom app.database import Base, engine\nBase.metadata.create_all(bind=engine)\n"


def _auto_fix_missing_pydantic_import(content: str) -> str:
    """Add 'from pydantic import BaseModel' if BaseModel is used but not imported."""
    if "BaseModel" not in content:
        return content
    if re.search(r'from\s+pydantic\s+import\b.*\bBaseModel\b', content):
        return content
    lines = content.splitlines(keepends=True)
    insert_idx = 0
    in_multiline = False
    for i, line in enumerate(lines):
        stripped_r = line.rstrip()
        stripped_l = line.lstrip()
        if in_multiline:
            if ")" in stripped_l:
                insert_idx = i + 1
                in_multiline = False
            continue
        if stripped_l.startswith("from ") or stripped_l.startswith("import "):
            if stripped_r.endswith("("):
                in_multiline = True
            else:
                insert_idx = i + 1
    lines.insert(insert_idx, "from pydantic import BaseModel\n")
    return "".join(lines)


def _normalize_newlines(path, content):
    if "\\n" in content and "\n" not in content:
        content = content.replace("\\n", "\n").replace("\\t", "\t")
    if path.endswith((".jsx", ".js")) and '\\"' in content:
        content = content.replace('\\"', '"')
    if path.endswith(".py"):
        from app.services.file_writer_service import (
            _repair_backslash_syntax, _add_extend_existing,
            _fix_pydantic_v1_patterns, _strip_auth_classes_from_schema,
            _strip_invalid_eager_loading, _fix_double_depends,
        )
        content = _repair_backslash_syntax(content)
        content = _add_extend_existing(content)
        content = _strip_auth_classes_from_schema(path, content)
        content = _fix_pydantic_v1_patterns(content)
        # Fix LLM typo: auth2_scheme (missing 'o') → oauth2_scheme
        if re.search(r'\bauth2_scheme\b', content):
            content = re.sub(r'\bauth2_scheme\b', 'oauth2_scheme', content)
        # Auto-add missing pydantic BaseModel import
        content = _auto_fix_missing_pydantic_import(content)
        # Fix FastAPI param ordering: body params must come before Path/Query/Depends
        from app.services.file_writer_service import _fix_fastapi_param_order
        content = _fix_fastapi_param_order(content)
        if "/routes/" in path.replace("\\", "/"):
            content = _strip_invalid_eager_loading(content)
            content = _fix_double_depends(content)
    if path.endswith((".jsx", ".js")):
        from app.services.file_writer_service import _fix_smart_quotes
        content = _fix_smart_quotes(content)
    if path.endswith(".py") and "schemas." in content:
        from app.services.file_writer_service import _fix_schemas_namespace
        content = _fix_schemas_namespace(content)
    return content


def _ensure_create_all(path, content):
    """Ensure main.py always calls Base.metadata.create_all so SQLite tables exist."""
    if not path.endswith("app/main.py") and not path.endswith("app\\main.py"):
        return content
    if "create_all" in content:
        return content
    lines = content.splitlines(keepends=True)
    insert_idx = 0
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith("import ") or stripped.startswith("from "):
            insert_idx = i + 1
    lines.insert(insert_idx, _CREATE_ALL_SNIPPET)
    return "".join(lines)


def _is_safe_to_write(path, content):
    """Refuse to write Python files that don't even parse. This is
    the truncation/completeness guard — a broken LLM response should
    never silently overwrite a working file."""

    if not path.endswith(".py"):
        return True

    try:
        ast.parse(content)
        return True

    except SyntaxError as e:
        print(f"\n=== REFUSING TRUNCATED/INVALID WRITE: {path} ===")
        print(f"SyntaxError: {e}")
        return False


# ── Exp064: narrow semantic-consistency guard ────────────────────────────
#
# Confirmed root cause (Exp063, docs/EXP063_PYDANTIC_ROOT_CAUSE.md): a
# runtime-fix LLM response can return an "entire corrected file" that is
# perfectly valid Python (passes _is_safe_to_write) but is internally
# self-inconsistent -- a route handler reads `req.username` while the
# SAME file's `SignupRequest(BaseModel)` class never declares a
# `username` field. write_fix's existing guard only checks syntax; this
# adds exactly one more check, scoped to exactly this failure class:
# "every attribute access on a locally-typed Pydantic request parameter
# resolves to a field that class actually declares, IN THE SAME FILE."
#
# Deliberately NOT a generalized semantic analyzer: no cross-file
# resolution, no type inference beyond a bare `Name` (or `Name | None` /
# `Optional[Name]`) parameter annotation, no attempt to check anything
# other than Pydantic BaseModel request parameters. If nothing in the
# file looks like this shape, the check is a no-op.

_PYDANTIC_RESERVED_ATTRS = {
    "model_config", "model_fields", "model_dump", "model_dump_json",
    "model_copy", "model_validate", "model_json_schema", "model_construct",
    "model_fields_set", "model_extra", "dict", "json", "Config",
    "schema", "schema_json", "copy", "parse_obj",
}


def _collect_basemodel_classes(tree: "ast.Module") -> dict:
    """
    Map class name -> set of declared field names, for every class in
    this file that inherits from BaseModel -- directly, or transitively
    through another locally-defined BaseModel subclass in the SAME file
    (a fixed-point resolution over local inheritance only; a base class
    imported from elsewhere is not resolved, matching the "no cross-file
    resolution" scope limit).
    """
    raw: dict = {}  # name -> {"bases": set[str], "own_fields": set[str]}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        bases = {b.id for b in node.bases if isinstance(b, ast.Name)}
        own_fields: set = set()
        for item in node.body:
            if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                own_fields.add(item.target.id)
            elif isinstance(item, ast.Assign):
                for t in item.targets:
                    if isinstance(t, ast.Name):
                        own_fields.add(t.id)
            elif isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # Methods/validators/properties are legitimately accessible
                # attributes too -- count them as "declared" to avoid a
                # false positive on e.g. a @property or a custom method.
                own_fields.add(item.name)
        raw[node.name] = {"bases": bases, "own_fields": own_fields}

    is_pydantic = {name: ("BaseModel" in info["bases"]) for name, info in raw.items()}
    # Fixed-point: a class is Pydantic if it inherits BaseModel directly,
    # or inherits a class in `raw` that's (transitively) Pydantic.
    changed = True
    while changed:
        changed = False
        for name, info in raw.items():
            if is_pydantic[name]:
                continue
            if any(is_pydantic.get(b) for b in info["bases"]):
                is_pydantic[name] = True
                changed = True

    resolved: dict = {}
    for name in raw:
        if not is_pydantic[name]:
            continue
        fields: set = set()
        seen: set = set()

        def _accumulate(cls_name):
            if cls_name in seen or cls_name not in raw:
                return
            seen.add(cls_name)
            fields.update(raw[cls_name]["own_fields"])
            for b in raw[cls_name]["bases"]:
                _accumulate(b)

        _accumulate(name)
        resolved[name] = fields
    return resolved


def _annotation_class_name(annotation, known_classes: dict) -> "str | None":
    """
    Resolve a parameter annotation to one of `known_classes`' names, for
    the shapes this narrow check supports: a bare `Name` (the confirmed
    failure case), or `Optional[Name]` / `Name | None` (a common FastAPI
    variant of the same shape). Anything else (List[X], Dict[...], a
    name not in known_classes, ...) resolves to None -- out of scope,
    not flagged, not an error.
    """
    if annotation is None:
        return None
    if isinstance(annotation, ast.Name):
        return annotation.id if annotation.id in known_classes else None
    if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
        for side in (annotation.left, annotation.right):
            name = _annotation_class_name(side, known_classes)
            if name:
                return name
        return None
    if isinstance(annotation, ast.Subscript):
        base = annotation.value
        is_optional_or_union = (
            (isinstance(base, ast.Name) and base.id in ("Optional", "Union"))
            or (isinstance(base, ast.Attribute) and base.attr in ("Optional", "Union"))
        )
        if is_optional_or_union:
            sl = annotation.slice
            elts = sl.elts if isinstance(sl, ast.Tuple) else [sl]
            for e in elts:
                name = _annotation_class_name(e, known_classes)
                if name:
                    return name
        return None
    return None


def _shadows_name(func_or_lambda, name: str) -> bool:
    args = func_or_lambda.args
    all_params = list(args.args) + list(args.posonlyargs) + list(args.kwonlyargs)
    if args.vararg:
        all_params.append(args.vararg)
    if args.kwarg:
        all_params.append(args.kwarg)
    return any(a.arg == name for a in all_params)


def _collect_param_attribute_accesses(func_node, param_name: str) -> list:
    """
    Walk func_node's body collecting every `param_name.attr` access
    (including inside f-strings, comprehensions, nested if/for/with/try
    blocks -- none of those open a new scope in Python). Correctly
    stops descending into a NESTED function/lambda that re-binds
    param_name as its own parameter (shadowing) -- that nested scope
    refers to a different object and is checked independently when the
    outer scan reaches it as its own function entry.
    """
    accesses = []

    def _visit(node):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) \
                and node.value.id == param_name:
            accesses.append((node.attr, getattr(node, "lineno", None)))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            if _shadows_name(node, param_name):
                return  # shadowed in this nested scope -- do not descend
        for child in ast.iter_child_nodes(node):
            _visit(child)

    for stmt in func_node.body:
        _visit(stmt)
    return accesses


def _check_request_field_consistency(path: str, content: str):
    """
    Exp064's actual gate. Returns (True, None) when there's nothing to
    flag (not a .py file, doesn't parse -- _is_safe_to_write already
    owns that failure mode, no Pydantic request-typed parameters found
    at all, or every access checks out). Returns (False, reason) on a
    confirmed attribute/field mismatch, `reason` describing exactly
    which access and which declared fields it didn't match.
    """
    if not path.endswith(".py"):
        return True, None

    try:
        tree = ast.parse(content)
    except SyntaxError:
        return True, None  # not this check's job to report -- _is_safe_to_write does

    classes = _collect_basemodel_classes(tree)
    if not classes:
        return True, None

    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        all_params = list(func.args.args) + list(func.args.posonlyargs) + list(func.args.kwonlyargs)
        for arg in all_params:
            class_name = _annotation_class_name(arg.annotation, classes)
            if not class_name:
                continue
            declared = classes[class_name]
            for attr_name, lineno in _collect_param_attribute_accesses(func, arg.arg):
                if attr_name in declared or attr_name in _PYDANTIC_RESERVED_ATTRS:
                    continue
                if attr_name.startswith("__") and attr_name.endswith("__"):
                    continue  # dunder attributes (e.g. __class__) are never fields
                loc = f"{path}:{lineno}" if lineno else path
                reason = (
                    f"{loc}: '{arg.arg}.{attr_name}' accessed in {func.name}(), but "
                    f"class {class_name} (defined in this same file) has no field "
                    f"'{attr_name}' -- declared fields: {sorted(declared)}"
                )
                return False, reason

    return True, None


def write_fix(project_path, fix):

    path = fix.get("path")
    content = fix.get("content")

    if not path or not content:
        print("write_fix called with missing path/content, skipping.")
        return False

    # Exp066: block path traversal attacks via the shared, pathlib-only
    # validator (app/utils/safe_path.py) instead of the previous inline
    # norm.startswith("..")/os.path.isabs check -- same accept/reject
    # outcome for every case that check already covered (verified by
    # regression test), plus new coverage for symlink escapes, Windows
    # drive-letter paths, and UNC paths that the old check missed.
    try:
        full_path = resolve_safe_path(project_path, path)
    except PathTraversalError as e:
        print(f"write_fix: blocked suspicious path: {path!r} ({e})")
        return False

    # Always inject the known-good database.py — never let LLM fixes overwrite it
    norm_fwd = path.replace("\\", "/")
    if norm_fwd == "app/database.py":
        from app.services.database_patcher import patch_database_py
        patch_database_py(project_path)
        return True

    content = _normalize_newlines(path, content)
    content = _ensure_create_all(path, content)

    if not _is_safe_to_write(path, content):
        return False

    _consistent, _reason = _check_request_field_consistency(path, content)
    if not _consistent:
        print(f"\n=== REFUSING SEMANTICALLY INCONSISTENT WRITE: {path} ===")
        print(f"  {_reason}")
        return False

    parent_dir = full_path.parent

    # When writing a nested module like app/utils/auth.py, Python requires app/utils/
    # to be a package directory — not a flat app/utils.py file. Remove the conflicting
    # flat file and create __init__.py so the directory becomes a proper package.
    flat_conflict = str(parent_dir) + ".py"
    if os.path.isfile(flat_conflict):
        os.remove(flat_conflict)
        print(f"  [fix_writer] removed conflicting flat module {os.path.relpath(flat_conflict, project_path)}")

    os.makedirs(
        parent_dir,
        exist_ok=True
    )

    # Ensure the package __init__.py exists so Python treats the directory as a package
    init_file = parent_dir / "__init__.py"
    if not init_file.exists():
        atomic_write_text(init_file, "")

    # Exp066: atomic write (temp file + os.replace) instead of a direct
    # open(..., "w") -- see app/utils/atomic_write.py's module docstring.
    atomic_write_text(full_path, content)

    return True