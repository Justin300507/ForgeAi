import ast
import os
import re

from app.core.context import Diagnostic, ErrorCategory, ErrorSeverity


def _add_schema(errors, diagnostics, msg, rel_path):
    errors.append(msg)
    if diagnostics is not None:
        # category=CONTRACT, severity=MEDIUM: exact parity with engine.py's
        # existing "schema" substring match (category) and default (severity).
        diagnostics.append(Diagnostic(
            error_id=Diagnostic.make_id("static", ErrorCategory.CONTRACT, msg, rel_path),
            category=ErrorCategory.CONTRACT,
            severity=ErrorSeverity.MEDIUM,
            source="static",
            message=msg,
            file_path=rel_path,
            validator_name="validate_schema_model_consistency",
        ))


_RESPONSE_MODEL_RE = re.compile(
    r"response_model\s*=\s*(?:List\[|list\[)?(\w+)\]?"
)


def _collect_response_model_schemas(project_path):
    """Schema class names actually used as `response_model=` in a route —
    only these are serialized directly from an ORM instance, so only these
    can raise ResponseValidationError for a field the model lacks entirely.
    Create/Update input schemas routinely have fields with no matching
    column (e.g. `password` on a UserCreate) and must not be flagged."""

    names = set()
    routes_dir = os.path.join(project_path, "app", "routes")

    if not os.path.isdir(routes_dir):
        return names

    for file in os.listdir(routes_dir):
        if not file.endswith(".py"):
            continue
        try:
            with open(os.path.join(routes_dir, file), "r", encoding="utf-8") as f:
                content = f.read()
        except Exception:
            continue
        names.update(_RESPONSE_MODEL_RE.findall(content))

    return names


def _is_optional_annotation(annotation):

    if isinstance(annotation, ast.Subscript):

        base = annotation.value

        if isinstance(base, ast.Name) and base.id == "Optional":
            return True

        if isinstance(base, ast.Attribute) and base.attr == "Optional":
            return True

        is_union = (
            (isinstance(base, ast.Name) and base.id == "Union")
            or (isinstance(base, ast.Attribute) and base.attr == "Union")
        )

        if is_union:

            sl = annotation.slice
            elts = sl.elts if isinstance(sl, ast.Tuple) else [sl]

            for e in elts:
                if isinstance(e, ast.Constant) and e.value is None:
                    return True

    if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):

        for side in (annotation.left, annotation.right):
            if isinstance(side, ast.Constant) and side.value is None:
                return True

    return False


# Self-ownership FK targets a create route conventionally fills in from the
# authenticated user / request context rather than the client body (e.g.
# `user_id=current_user.id` inline in the handler) -- these are legitimately
# absent from a Create schema and must not be flagged by the missing-FK
# check below.
_SELF_OWNERSHIP_FK_TABLES = {"users", "user", "accounts", "account"}


def _column_call_info(call_node):
    """Given the ast.Call for a Column(...)/mapped_column(...) declaration,
    return (nullable, has_default, fk_table). fk_table is the table name a
    ForeignKey(...) argument targets (e.g. "categories.id" -> "categories"),
    or None if this column isn't a foreign key."""

    nullable = False
    has_default = False
    fk_table = None

    for kw in call_node.keywords:
        if kw.arg in ("nullable",) and isinstance(kw.value, ast.Constant):
            nullable = kw.value.value
        if kw.arg in ("default", "server_default"):
            has_default = True
        if kw.arg == "primary_key" and isinstance(kw.value, ast.Constant) and kw.value.value:
            has_default = True  # autoincrement PK -- never client-supplied

    for arg in call_node.args:
        if (
            isinstance(arg, ast.Call)
            and hasattr(arg.func, "id")
            and arg.func.id == "ForeignKey"
            and arg.args
            and isinstance(arg.args[0], ast.Constant)
            and isinstance(arg.args[0].value, str)
        ):
            fk_table = arg.args[0].value.split(".")[0]

    return nullable, has_default, fk_table


def validate_schema_model_consistency(
    project_path,
    errors,
    diagnostics=None
):

    models = {}
    # field -> (has_default, fk_table); populated alongside `models[...]`
    # (which stays field -> nullable for backward compatibility with the
    # existing checks below).
    model_field_meta = {}
    schemas = {}

    for root, _, files in os.walk(project_path):

        for file in files:

            if not file.endswith(".py"):
                continue

            path = os.path.join(root, file)

            try:

                with open(
                    path,
                    "r",
                    encoding="utf-8"
                ) as f:
                    content = f.read()

                tree = ast.parse(content)

            except Exception:
                continue

            if "models" in path.lower():

                for node in ast.walk(tree):

                    if not isinstance(node, ast.ClassDef):
                        continue

                    fields = {}
                    field_meta = {}

                    for child in node.body:

                        if (
                            isinstance(child, ast.Assign)
                            and isinstance(child.value, ast.Call)
                        ):

                            if (
                                hasattr(child.value.func, "id")
                                and child.value.func.id == "Column"
                            ):

                                nullable, has_default, fk_table = _column_call_info(child.value)

                                for target in child.targets:

                                    if isinstance(
                                        target,
                                        ast.Name
                                    ):
                                        fields[target.id] = nullable
                                        field_meta[target.id] = (has_default, fk_table)

                        # SQLAlchemy 2.0 typed-declarative style:
                        # `name: Mapped[int] = mapped_column(...)`.
                        elif (
                            isinstance(child, ast.AnnAssign)
                            and isinstance(child.target, ast.Name)
                            and isinstance(child.value, ast.Call)
                            and hasattr(child.value.func, "id")
                            and child.value.func.id == "mapped_column"
                        ):

                            nullable, has_default, fk_table = _column_call_info(child.value)

                            fields[child.target.id] = nullable
                            field_meta[child.target.id] = (has_default, fk_table)

                        # A `@property` (e.g. an `id` alias for a
                        # differently-named primary key) is a legitimate,
                        # always-present attribute as far as Pydantic's
                        # `from_attributes` serialization is concerned --
                        # without this, database_patcher's alias fix for a
                        # missing `id` column never clears this validator's
                        # error and the fix loop stalls forever re-reporting
                        # the same "does not exist as a column" mismatch.
                        elif (
                            isinstance(child, ast.FunctionDef)
                            and child.name not in fields
                            and any(
                                isinstance(dec, ast.Name) and dec.id == "property"
                                for dec in child.decorator_list
                            )
                        ):
                            fields[child.name] = False
                            field_meta[child.name] = (True, None)

                    models[node.name] = fields
                    model_field_meta[node.name] = field_meta

            if "schemas" in path.lower():

                for node in ast.walk(tree):

                    if not isinstance(node, ast.ClassDef):
                        continue

                    fields = {}

                    for child in node.body:

                        if isinstance(
                            child,
                            ast.AnnAssign
                        ):

                            if isinstance(
                                child.target,
                                ast.Name
                            ):

                                is_optional = _is_optional_annotation(
                                    child.annotation
                                )

                                fields[child.target.id] = not is_optional

                    schemas[node.name] = {
                        "file": os.path.relpath(
                            path, project_path
                        ).replace(os.sep, "/"),
                        "fields": fields
                    }

    response_model_schemas = _collect_response_model_schemas(project_path)

    for model_name, model_fields in models.items():

        for schema_name, schema_info in schemas.items():

            # Schemas are always named as the model name plus a suffix (TaskCreate,
            # TaskUpdate, ...) per the project's naming contract — require a prefix
            # match, not a bare substring, or e.g. model "Item" wrongly pairs with
            # unrelated schemas like "LineItemRead" or "WorkItemBase".
            if not schema_name.lower().startswith(model_name.lower()):
                continue

            schema_fields = schema_info["fields"]
            schema_file = schema_info["file"]

            for field, nullable in model_fields.items():

                if (
                    nullable
                    and field in schema_fields
                    and schema_fields[field]
                ):

                    _add_schema(errors, diagnostics,
                        f"Schema mismatch: {schema_file}: "
                        f"{schema_name}.{field} required but model allows NULL",
                        schema_file)

            # A field required by a response_model schema but entirely absent
            # from the model is a guaranteed ResponseValidationError at
            # request time (FastAPI can't serialize a field that doesn't
            # exist on the ORM instance) — not just a nullable/required
            # mismatch. Scoped to response_model schemas only: Create/Update
            # input schemas legitimately have fields with no column (e.g.
            # `password` on a UserCreate).
            if schema_name in response_model_schemas:

                for field, required in schema_fields.items():

                    if required and field not in model_fields:

                        _add_schema(errors, diagnostics,
                            f"Schema mismatch: {schema_file}: "
                            f"{schema_name}.{field} is required in the response "
                            f"schema but '{field}' does not exist as a column on "
                            f"the {model_name} model",
                            schema_file)

            # A NOT NULL, no-default foreign key to some OTHER resource (not
            # the auth/user table) that the Create schema never exposes at
            # all is a guaranteed IntegrityError on every single create call
            # -- there is no value the client could ever send for it, since
            # the schema doesn't even accept the field (live case:
            # personal_expense_tracker, 2026-07-16 -- Expense.category_id is
            # nullable=False but ExpenseCreate has no category_id field at
            # all; every POST /expenses 500'd with a NOT NULL/FK
            # IntegrityError, the fix loop patched other files 5 times, and
            # the deploy score plateaued in the low 70s without ever finding
            # this). Scoped to Create schemas only -- Update schemas
            # legitimately omit FKs that shouldn't change after creation.
            if schema_name.endswith("Create"):

                field_meta = model_field_meta.get(model_name, {})

                for field, nullable in model_fields.items():

                    if nullable or field in schema_fields:
                        continue

                    has_default, fk_table = field_meta.get(field, (False, None))

                    if has_default or not fk_table:
                        continue

                    if fk_table.lower() in _SELF_OWNERSHIP_FK_TABLES:
                        continue

                    _add_schema(errors, diagnostics,
                        f"Schema mismatch: {schema_file}: "
                        f"{schema_name} is missing '{field}' — the {model_name} "
                        f"model requires it (NOT NULL, references {fk_table}) "
                        f"but the Create schema never exposes it, so every "
                        f"create call will fail with a database IntegrityError",
                        schema_file)