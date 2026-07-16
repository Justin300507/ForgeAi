"""
Flags a ForeignKey("table.column") string reference whose column doesn't
actually exist on the target table -- a guaranteed sqlalchemy.exc.
NoReferencedColumnError at DB-init time (the whole app fails to start,
before a single endpoint can be hit).

Root cause this catches: when a model's own primary key isn't named "id"
(e.g. `patient_id = Column(Integer, primary_key=True)` on Patient, table
"patients"), a DIFFERENT model's FK column generated in a separate parallel
LLM call routinely still guesses ForeignKey("patients.id") -- the generic,
far more common convention -- because that call has no visibility into
patients.py's actual column names. Reproduced live (hospital_management,
2026-07-16): Admission.patient_id referenced ForeignKey("patients.id") and
Admission.bed_id referenced ForeignKey("beds.id"); neither "patients" nor
"beds" has an "id" column, both use a "{singular}_id" primary key instead.
The app crashed at Base.metadata.create_all() on every single runtime
attempt -- CRUD/browser/integration never even got a chance to run, capping
the whole app's score in the 40s despite otherwise-clean generated code.
"""
import ast
import os

from app.core.context import Diagnostic, ErrorCategory, ErrorSeverity


def _collect_table_columns(project_path):
    """AST-walk app/models/*.py -> {table_name: set(real column names)},
    keyed by the SQL table name (__tablename__), not the Python class name --
    ForeignKey strings reference tables, not classes."""
    models_dir = os.path.join(project_path, "app", "models")
    table_columns = {}
    if not os.path.isdir(models_dir):
        return table_columns

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

            table_name = None
            columns = set()
            for child in node.body:
                if (
                    isinstance(child, ast.Assign)
                    and len(child.targets) == 1
                    and isinstance(child.targets[0], ast.Name)
                    and child.targets[0].id == "__tablename__"
                    and isinstance(child.value, ast.Constant)
                ):
                    table_name = child.value.value
                elif (
                    isinstance(child, ast.Assign)
                    and isinstance(child.value, ast.Call)
                    and hasattr(child.value.func, "id")
                    and child.value.func.id == "Column"
                ):
                    for target in child.targets:
                        if isinstance(target, ast.Name):
                            columns.add(target.id)
                elif (
                    isinstance(child, ast.AnnAssign)
                    and isinstance(child.target, ast.Name)
                    and isinstance(child.value, ast.Call)
                    and hasattr(child.value.func, "id")
                    and child.value.func.id == "mapped_column"
                ):
                    columns.add(child.target.id)

            if table_name:
                table_columns.setdefault(table_name, set()).update(columns)

    return table_columns


def validate_foreign_key_targets(project_path, errors, diagnostics=None):
    models_dir = os.path.join(project_path, "app", "models")
    if not os.path.isdir(models_dir):
        return

    table_columns = _collect_table_columns(project_path)
    if not table_columns:
        return

    reported = set()
    for file in os.listdir(models_dir):
        if not file.endswith(".py"):
            continue
        rel_path = f"app/models/{file}"
        path = os.path.join(models_dir, file)
        try:
            with open(path, "r", encoding="utf-8") as f:
                tree = ast.parse(f.read())
        except Exception:
            continue

        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and hasattr(node.func, "id")
                and node.func.id == "ForeignKey"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
                and "." in node.args[0].value
            ):
                continue

            target_table, _, target_column = node.args[0].value.partition(".")
            if target_table not in table_columns:
                continue  # target defined outside this project's models (or a typo we can't verify)
            if target_column in table_columns[target_table]:
                continue

            key = (target_table, target_column)
            if key in reported:
                continue
            reported.add(key)

            real_cols = ", ".join(sorted(table_columns[target_table])[:10])
            msg = (
                f"Broken foreign key: ForeignKey(\"{target_table}.{target_column}\") "
                f"in {rel_path} -- table '{target_table}' has no column named "
                f"'{target_column}' (its real columns: {real_cols}). This crashes "
                f"the entire app at startup (NoReferencedColumnError), before any "
                f"endpoint can run."
            )
            errors.append(msg)
            if diagnostics is not None:
                diagnostics.append(Diagnostic(
                    error_id=Diagnostic.make_id("static", ErrorCategory.RUNTIME, msg, rel_path),
                    category=ErrorCategory.RUNTIME,
                    severity=ErrorSeverity.CRITICAL,
                    source="static",
                    message=msg,
                    file_path=rel_path,
                    validator_name="validate_foreign_key_targets",
                ))
