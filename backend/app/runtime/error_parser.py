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

        return {
            "type": "ImportError",
            "missing_symbol": (
                symbol_match.group(1)
                if symbol_match
                else None
            ),
            "import_target_module": (
                target_match.group(1)
                if target_match
                else None
            ),
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

    if "ValidationError" in stderr:

        return {
            "type": "ValidationError",
            "error_file": error_file
        }

    if "FastAPIError" in stderr:

        return {
            "type": "FastAPIError",
            "error_file": error_file
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

    # NOT NULL constraint on timestamp columns — model missing server_default
    if "IntegrityError" in stderr and "NOT NULL constraint failed" in stderr:
        col_match = re.search(r"NOT NULL constraint failed: (\S+)", stderr)
        col = col_match.group(1) if col_match else None
        table = col.split(".")[0] if col else None
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

    return {
        "type": "Unknown",
        "raw_error": stderr[:1000],
        "error_file": error_file
    }