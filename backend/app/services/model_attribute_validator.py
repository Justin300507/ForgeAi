"""
Flags route code that accesses a SQLAlchemy model attribute (ModelName.attr)
which isn't actually a column, @property, or method on that model.

Root cause this catches: backend route files are generated in parallel, one
LLM call per file (see v6_orchestrator.py's Wave 4), so a route file has no
visibility into another file's exact model schema. Reproduced live
(habit_tracker, 2026-07-16): stats_routes.py referenced HabitLog.created_at
and seed_routes.py referenced HabitLog.timestamp -- neither exists, the real
column is completed_at -- both are plausible-sounding guesses made without
seeing the model. This crashes at request time (AttributeError), and today
that's only ever caught by the fix loop reacting to an actual runtime crash,
one file at a time, burning repair-attempt budget on a class of bug a single
static pass can catch across every route file at once.
"""
import ast
import os
import re

from app.core.context import Diagnostic, ErrorCategory, ErrorSeverity

_SQLA_SPECIAL_ATTRS = {
    "query", "metadata", "__table__", "__tablename__", "__table_args__",
    "__mapper__", "__dict__", "__class__", "__init__", "__name__",
}

_MODEL_ATTR_RE = re.compile(r'\b([A-Z]\w*)\.(\w+)')
_MODEL_IMPORT_RE = re.compile(r'from\s+app\.models\.\w+\s+import\s+([A-Z]\w*(?:\s*,\s*[A-Z]\w*)*)')


def _is_safely_wrapped(tree: ast.AST, lineno: int) -> bool:
    """True if `lineno` sits inside a try-block whose handler broadly
    catches Exception -- the exact shape deterministic_patcher.py's
    _patch_invalid_model_attribute_access produces (try: <original body> /
    except Exception: return {}).

    Reproduced live (habit_tracker, 2026-07-24): this validator kept
    re-reporting the same "Invalid attribute access" error identically
    across every fix-loop attempt in both a V6 pass and a full V7
    regeneration, because the wrapper patch neutralizes the crash but never
    removes the offending line of text -- so a purely textual/AST re-scan
    finds it again every time. That's correct as a crash-prevention measure
    but wrong as a fix-loop exit signal: the app can no longer 500 on this
    line, so re-flagging it as blocking just burns LLM fix-attempt budget
    that could go toward errors that are actually still fixable.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try) or not node.body:
            continue
        body_start = node.body[0].lineno
        body_end = max(getattr(n, "end_lineno", n.lineno) or n.lineno for n in node.body)
        if not (body_start <= lineno <= body_end):
            continue
        for handler in node.handlers:
            if handler.type is None:
                return True  # bare `except:`
            if isinstance(handler.type, ast.Name) and handler.type.id == "Exception":
                return True
    return False


def _collect_model_attrs(project_path):
    """AST-walk app/models/*.py -> {ModelClassName: set(valid attribute names)}.
    Column/mapped_column assignments, @property methods, and any other method
    all count as valid -- this stays deliberately permissive (false negatives
    are fine, false positives are not)."""
    models_dir = os.path.join(project_path, "app", "models")
    model_attrs = {}
    if not os.path.isdir(models_dir):
        return model_attrs

    for file in os.listdir(models_dir):
        if not file.endswith(".py"):
            continue
        path = os.path.join(models_dir, file)
        try:
            with open(path, "r", encoding="utf-8") as f:
                tree = ast.parse(f.read())
        except Exception:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            attrs = set()
            for child in node.body:
                if isinstance(child, ast.Assign):
                    for target in child.targets:
                        if isinstance(target, ast.Name):
                            attrs.add(target.id)
                elif isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name):
                    attrs.add(child.target.id)
                elif isinstance(child, ast.FunctionDef):
                    attrs.add(child.name)
            # A class can be reopened across shim files (app/models/habit.py
            # shimming app/models/habits.py) -- union rather than overwrite.
            model_attrs.setdefault(node.name, set()).update(attrs)

    return model_attrs


def validate_model_attribute_access(project_path, errors, diagnostics=None):
    model_attrs = _collect_model_attrs(project_path)
    if not model_attrs:
        return

    routes_dir = os.path.join(project_path, "app", "routes")
    if not os.path.isdir(routes_dir):
        return

    reported = set()
    for file in os.listdir(routes_dir):
        if not file.endswith(".py"):
            continue
        rel_path = f"app/routes/{file}"
        path = os.path.join(routes_dir, file)
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception:
            continue

        # Only check models this specific file actually imports from
        # app.models -- guards against a same-named local variable or an
        # unrelated class elsewhere producing a false positive.
        imported_models = set()
        for m in _MODEL_IMPORT_RE.finditer(content):
            imported_models.update(n.strip() for n in m.group(1).split(","))

        try:
            tree = ast.parse(content)
        except SyntaxError:
            tree = None  # can't verify safety -- fall back to reporting as before

        for m in _MODEL_ATTR_RE.finditer(content):
            model_name, attr = m.group(1), m.group(2)
            if model_name not in imported_models or model_name not in model_attrs:
                continue
            if attr in _SQLA_SPECIAL_ATTRS or attr.startswith("__"):
                continue
            if attr in model_attrs[model_name]:
                continue
            if tree is not None:
                lineno = content.count("\n", 0, m.start()) + 1
                if _is_safely_wrapped(tree, lineno):
                    continue

            key = (file, model_name, attr)
            if key in reported:
                continue
            reported.add(key)

            valid = ", ".join(sorted(model_attrs[model_name])[:12])
            msg = (
                f"Invalid attribute access: {model_name}.{attr} in {rel_path} -- "
                f"'{attr}' is not a column, property, or method on the "
                f"{model_name} model (valid: {valid})"
            )
            errors.append(msg)
            if diagnostics is not None:
                diagnostics.append(Diagnostic(
                    error_id=Diagnostic.make_id("static", ErrorCategory.RUNTIME, msg, rel_path),
                    category=ErrorCategory.RUNTIME,
                    severity=ErrorSeverity.HIGH,
                    source="static",
                    message=msg,
                    file_path=rel_path,
                    validator_name="validate_model_attribute_access",
                ))
