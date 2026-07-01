import ast
import os
import re


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


def validate_schema_model_consistency(
    project_path,
    errors
):

    models = {}
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

                    for child in node.body:

                        if (
                            isinstance(child, ast.Assign)
                            and isinstance(child.value, ast.Call)
                        ):

                            if (
                                hasattr(child.value.func, "id")
                                and child.value.func.id == "Column"
                            ):

                                nullable = False

                                for kw in child.value.keywords:

                                    if kw.arg == "nullable":

                                        if (
                                            isinstance(
                                                kw.value,
                                                ast.Constant
                                            )
                                        ):
                                            nullable = kw.value.value

                                for target in child.targets:

                                    if isinstance(
                                        target,
                                        ast.Name
                                    ):
                                        fields[target.id] = nullable

                    models[node.name] = fields

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

                    errors.append(
                        f"Schema mismatch: {schema_file}: "
                        f"{schema_name}.{field} required but model allows NULL"
                    )

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

                        errors.append(
                            f"Schema mismatch: {schema_file}: "
                            f"{schema_name}.{field} is required in the response "
                            f"schema but '{field}' does not exist as a column on "
                            f"the {model_name} model"
                        )