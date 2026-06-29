import re


def _extract_error_file(stderr):
    """Find the file that was executing when the failure occurred.
    Prefers files inside generated_projects/ so we fix our own code
    rather than patching third-party libraries when the error is
    triggered by a wrong attribute access in generated code."""

    matches = re.findall(r'File "(.+?)", line \d+', stderr)
    if not matches:
        return None

    # Prefer any file inside a generated project
    for match in reversed(matches):
        if "generated_projects" in match:
            return match

    return matches[-1]


def parse_runtime_error(stderr):

    stderr = str(stderr)
    error_file = _extract_error_file(stderr)

    if "python-multipart" in stderr:

        return {
            "type": "MissingDependency",
            "dependency": "python-multipart",
            "error_file": error_file
        }

    if "email-validator" in stderr:

        return {
            "type": "MissingDependency",
            "dependency": "email-validator",
            "error_file": error_file
        }

    if "No module named" in stderr:

        match = re.search(
            r"No module named ['\"](.+?)['\"]",
            stderr
        )
        module_name = match.group(1) if match else None

        # Specific: werkzeug is a Flask dependency, should use passlib
        if module_name == "werkzeug" or (module_name and "werkzeug" in module_name):
            return {
                "type": "WerkzeugImportError",
                "module": module_name,
                "error_file": error_file,
                "hint": "werkzeug is a Flask library, not available here. Use passlib[bcrypt] instead: `from passlib.context import CryptContext`"
            }

        # Specific: monolithic schemas.py — route imports from app.schemas.X but only schemas.py exists
        if module_name and module_name.startswith("app.schemas.") and module_name != "app.schemas.schemas":
            schema_name = module_name.split(".")[-1]  # e.g. "account"
            return {
                "type": "MonolithicSchemaError",
                "missing_module": module_name,
                "schema_name": schema_name,
                "error_file": error_file,
                "hint": (
                    f"Route imports from '{module_name}' but only 'app/schemas/schemas.py' exists. "
                    f"Create 'app/schemas/{schema_name}.py' with the relevant Pydantic classes, "
                    "OR change the import in the route file to 'from app.schemas.schemas import ...'"
                ),
            }

        return {
            "type": "ModuleNotFoundError",
            "module": module_name,
            "error_file": error_file
        }

    if (
        "partially initialized module"
        in stderr
    ):

        return {
            "type": "CircularImport",
            "error_file": error_file
        }

    if "cannot import name" in stderr:

        symbol_match = re.search(
            r"cannot import name ['\"](.+?)['\"]",
            stderr
        )
        target_match = re.search(
            r"from ['\"](.+?)['\"]",
            stderr
        )
        missing_symbol = symbol_match.group(1) if symbol_match else None
        import_target = target_match.group(1) if target_match else None

        _AUTH_HELPERS = {
            "get_user_by_email", "get_user_by_username", "get_user_by_id",
            "authenticate_user", "verify_password", "get_password_hash",
            "hash_password", "check_password", "create_access_token",
            "decode_token", "decode_access_token", "verify_token",
        }
        if missing_symbol in _AUTH_HELPERS and import_target and (
            "auth" in import_target or "user" in import_target
        ):
            return {
                "type": "MissingAuthHelperError",
                "missing_symbol": missing_symbol,
                "import_target_module": import_target,
                "error_file": error_file,
                "hint": (
                    f"'{missing_symbol}' is not defined in '{import_target}'. "
                    "Define it there, or import from the correct module. "
                    "Auth utilities (hash/verify/create_token) go in app/utils/auth.py. "
                    "User lookup functions (get_user_by_email) go in app/services/auth.py or app/services/user.py."
                ),
            }

        return {
            "type": "ImportError",
            "missing_symbol": missing_symbol,
            "import_target_module": import_target,
            "error_file": error_file
        }

    if "FileNotFoundError" in stderr:

        return {
            "type": "FileNotFoundError",
            "error_file": error_file
        }

    if "AttributeError" in stderr:
        # Specific: schemas namespace access (schemas.user.X)
        if "module 'app.schemas' has no attribute" in stderr:
            attr_m = re.search(r"has no attribute '(.+?)'", stderr)
            return {
                "type": "SchemasNamespaceError",
                "missing_attr": attr_m.group(1) if attr_m else None,
                "error_file": error_file,
                "hint": "Use direct imports: `from app.schemas.user import UserResponse` — never access schemas as a namespace object."
            }

        # Specific: async context manager on sync engine
        if "asynchronous context manager protocol" in stderr or "async with engine" in stderr:
            return {
                "type": "AsyncEngineError",
                "error_file": error_file,
                "hint": "Synchronous SQLAlchemy engines don't support `async with`. Use `with engine.begin()` or call Base.metadata.create_all(bind=engine) at module level."
            }

        # Specific: joinedload on non-relationship attribute
        if "joinedload" in stderr or "has no attribute" in stderr:
            attr_m = re.search(r"has no attribute '(.+?)'", stderr)
            return {
                "type": "RelationshipMissingError",
                "missing_attr": attr_m.group(1) if attr_m else None,
                "error_file": error_file,
                "hint": "You called joinedload() on an attribute that isn't a declared relationship(). Add the relationship to the model or remove the joinedload."
            }

        return {
            "type": "AttributeError",
            "error_file": error_file
        }

    # async context manager on a synchronous SQLAlchemy engine (TypeError)
    if "asynchronous context manager protocol" in stderr:
        return {
            "type": "AsyncEngineError",
            "error_file": error_file,
            "hint": "Synchronous SQLAlchemy engines don't support `async with`. Use `with engine.begin()` or call Base.metadata.create_all(bind=engine) at module level."
        }

    # TypeError: 'field_name' is an invalid keyword argument for ModelClass
    # This happens when a route passes a field that doesn't exist as a Column on the model
    if "TypeError" in stderr and "invalid keyword argument" in stderr:
        kwarg_m = re.search(
            r"TypeError: '(.+?)' is an invalid keyword argument for (\w+)",
            stderr
        )
        field_name = kwarg_m.group(1) if kwarg_m else None
        model_class = kwarg_m.group(2) if kwarg_m else None
        return {
            "type": "ModelFieldMismatchError",
            "missing_attr": field_name,
            "model_class": model_class,
            "error_file": error_file,
            "hint": (
                f"Route passes '{field_name}' to {model_class}() but that field is not a "
                f"Column on the model. Check app/models/ for the actual column names on "
                f"{model_class} and update the constructor call to use only valid columns."
            ),
        }

    if "ValidationError" in stderr:

        return {
            "type": "ValidationError",
            "error_file": error_file
        }

    if "FastAPIError" in stderr:
        # Specific: response_model is a SQLAlchemy model, not Pydantic schema
        hint = None
        if "is a valid Pydantic field type" in stderr or "response field" in stderr:
            hint = "response_model must be a Pydantic schema (e.g. UserResponse), not a SQLAlchemy model class (e.g. User)."
        return {
            "type": "FastAPIError",
            "error_file": error_file,
            "hint": hint,
        }

    if "Table" in stderr and "is already defined for this MetaData instance" in stderr:
        table_match = re.search(r"Table '(.+?)' is already defined", stderr)
        return {
            "type": "DuplicateTableError",
            "table": table_match.group(1) if table_match else None,
            "error_file": error_file,
            "hint": (
                "A model file is importing another model at module level, causing double table registration. "
                "Fix: model files must NOT import other model classes. Use ForeignKey('table.id') strings only. "
                "All model imports belong in main.py only."
            ),
        }

    if "Error loading ASGI app" in stderr:

        return {
            "type": "ASGIAppError",
            "error_file": error_file
        }

    if "SyntaxError" in stderr:

        return {
            "type": "SyntaxError",
            "error_file": error_file
        }

    # FastAPI startup: Depends() wrapping a raw built-in type (str, int, etc.)
    if "no signature found for builtin type" in stderr:
        builtin_match = re.search(
            r"no signature found for builtin type <class '(.+?)'>",
            stderr
        )
        return {
            "type": "InvalidDependsType",
            "builtin_type": builtin_match.group(1) if builtin_match else None,
            "error_file": error_file,
            "hint": "A Depends() is wrapping a bare built-in type. Replace with a proper callable dependency."
        }

    # SQLAlchemy FK resolution failure — a referenced table's model was never imported
    if "NoReferencedTableError" in stderr or "could not find table" in stderr:
        table_match = re.search(
            r"could not find table '(.+?)'",
            stderr
        )
        return {
            "type": "NoReferencedTableError",
            "missing_table": table_match.group(1) if table_match else None,
            "error_file": error_file,
            "hint": "A model with this table name is missing from the imports in main.py — add it so Base.metadata sees it."
        }

    # NOT NULL constraint — distinguish FK vs timestamp columns
    if "IntegrityError" in stderr and "NOT NULL constraint failed" in stderr:
        col_match = re.search(r"NOT NULL constraint failed: (\S+)", stderr)
        col = col_match.group(1) if col_match else None
        table = col.split(".")[0] if col else None
        col_name = col.split(".")[-1] if col else ""

        _TIMESTAMP_COLS = {"created_at", "updated_at", "deleted_at", "modified_at", "timestamp"}
        _FK_COLS_SUFFIXES = ("_id",)

        if col_name in _TIMESTAMP_COLS or col_name.endswith(("_at", "_date", "_time")):
            return {
                "type": "TimestampNotNullError",
                "column": col,
                "table": table,
                "error_file": error_file,
                "hint": (
                    f"Column '{col}' is NOT NULL but has no server_default. "
                    "Add: server_default=func.now() and onupdate=func.now() to the Column."
                ),
            }

        if col_name.endswith(_FK_COLS_SUFFIXES):
            return {
                "type": "UserIdNotInjectedError",
                "column": col,
                "table": table,
                "col_name": col_name,
                "error_file": error_file,
                "hint": (
                    f"Column '{col}' is NOT NULL (foreign key) but the route handler "
                    "doesn't inject it from auth. Add `current_user: User = Depends(get_current_user)` "
                    f"to the route and set `obj.{col_name} = current_user.id` before db.add()."
                ),
            }

        return {
            "type": "NotNullViolationError",
            "column": col,
            "table": table,
            "error_file": error_file,
            "hint": (
                f"Column '{col}' is NOT NULL but was inserted as NULL. "
                f"In app/models/ find the {table} model and add "
                f"server_default='active' (for strings) or nullable=True to the '{col_name}' column. "
                f"This prevents NOT NULL failures when the column isn't explicitly set by the route. "
                f"Do NOT modify pydantic or fastapi source files."
            ),
        }

    # SQLAlchemy relationship string resolution — model not imported in main.py
    if "InvalidRequestError" in stderr and ("failed to locate a name" in stderr or "expression" in stderr and "failed" in stderr):
        model_match = re.search(r"expression ['\"](.+?)['\"] failed to locate a name", stderr)
        return {
            "type": "RelationshipModelNotImported",
            "missing_model": model_match.group(1) if model_match else None,
            "error_file": error_file,
            "hint": (
                "A SQLAlchemy relationship() uses a string name but that model's file is not "
                "imported in main.py. Add the import: `from app.models.<name> import <Model>` "
                "to main.py so Base.metadata can resolve the relationship."
            ),
        }

    # postgres:// scheme used with SQLAlchemy 1.4+
    if "postgres://" in stderr and "did you mean" not in stderr:
        return {
            "type": "PostgresURLSchemeError",
            "error_file": error_file,
            "hint": "Change DATABASE_URL prefix from 'postgres://' to 'postgresql://' — SQLAlchemy 1.4+ dropped the old scheme."
        }

    # OperationalError: connection refused / server closed connection (pool issue)
    if "OperationalError" in stderr and (
        "could not connect to server" in stderr
        or "server closed the connection unexpectedly" in stderr
        or "SSL connection has been closed unexpectedly" in stderr
    ):
        return {
            "type": "DatabaseConnectionError",
            "error_file": error_file,
            "hint": "Add pool_pre_ping=True to create_engine() to recover from stale connections."
        }

    # General SQLAlchemy errors
    if "sqlalchemy.exc" in stderr:
        exc_match = re.search(r"sqlalchemy\.exc\.(\w+)", stderr)
        return {
            "type": "SQLAlchemyError",
            "exc_class": exc_match.group(1) if exc_match else None,
            "error_file": error_file
        }

    # Pydantic v1 orm_mode used with Pydantic v2
    if "orm_mode" in stderr and "from_attributes" in stderr:
        return {
            "type": "PydanticOrmModeDeprecated",
            "error_file": error_file,
            "hint": "Replace `orm_mode = True` with `from_attributes = True` in all Pydantic Config classes."
        }

    # PydanticSerializationError: route returns ORM object but schema lacks from_attributes
    # The traceback points into pydantic internals, not the generated route — redirect to a route file.
    if "PydanticSerializationError" in stderr and "Unable to serialize unknown type" in stderr:
        model_match = re.search(r"Unable to serialize unknown type: <class '(.+?)'>", stderr)
        orm_class = model_match.group(1) if model_match else None
        all_files = re.findall(r'File "(.+?)", line \d+', stderr)
        route_file = next((f for f in all_files if "generated_projects" in f and "/routes/" in f), None)
        return {
            "type": "PydanticSerializationError",
            "orm_class": orm_class,
            "error_file": route_file or error_file,
            "hint": (
                f"The response schema cannot serialize the SQLAlchemy ORM object ({orm_class}). "
                "Fix: add 'model_config = ConfigDict(from_attributes=True)' to EVERY Pydantic "
                "schema class in app/schemas/ that is used as a response_model. "
                "Also verify the route's response_model is a Pydantic schema (e.g. TodoResponse), "
                "not the SQLAlchemy model class (e.g. TodoItem). "
                "Do NOT modify pydantic source files."
            ),
        }

    return {
        "type": "Unknown",
        "raw_error": stderr[:1000],
        "error_file": error_file
    }