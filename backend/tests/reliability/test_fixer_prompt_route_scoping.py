"""
Regression tests for app/prompts/fixer_prompt.py's route-rule scoping.

Reproduced live (habit_tracker, 2026-07-25): build_fixer_prompt()
unconditionally included SYMBOL REPAIR RULES and ROUTE QUALITY RULES --
route-file-specific guidance containing a literal worked example
(`user_router = APIRouter()` followed by a full `get_users()` endpoint
stub) -- in the fix prompt for EVERY file type, including Pydantic schema
files. The real generated app/schemas/user.py came back with that exact
example appended verbatim to the end of the file (`user_router =
APIRouter()` + `@user_router.get("/")\ndef get_users(): return []`),
breaking a file that should only ever contain BaseModel classes. Made
worse by coincidence: the example's own name ("user_router") matched the
real project's own "User" domain.

Run directly: python tests/reliability/test_fixer_prompt_route_scoping.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.prompts.fixer_prompt import build_fixer_prompt


def test_route_rules_excluded_for_schema_file():
    prompt = build_fixer_prompt(
        "app/schemas/user.py",
        "class UserCreate(BaseModel):\n    username: str\n",
        ["Schema mismatch: UserCreate.username required but model allows NULL"],
    )
    assert "SYMBOL REPAIR RULES" not in prompt
    assert "ROUTE QUALITY RULES" not in prompt
    # The exact leaked content from the live incident -- a full,
    # copy-pasteable endpoint stub -- must be gone. FIXER_CONTRACT's own
    # much lower-risk one-line "WRONG/RIGHT" router-naming mention (a
    # shared, multi-purpose block used by several other prompt builders
    # too) is a separate, deliberately out-of-scope concern for this fix.
    assert "def get_users():" not in prompt
    assert "@user_router.get(\"/\")" not in prompt


def test_route_rules_excluded_for_model_file():
    prompt = build_fixer_prompt(
        "app/models/user.py",
        "class User(Base):\n    __tablename__ = 'users'\n",
        ["some model error"],
    )
    assert "SYMBOL REPAIR RULES" not in prompt
    assert "ROUTE QUALITY RULES" not in prompt


def test_route_rules_included_for_route_file():
    prompt = build_fixer_prompt(
        "app/routes/user_routes.py",
        "router = APIRouter()\n",
        ["Missing symbol 'user_router'"],
    )
    assert "SYMBOL REPAIR RULES" in prompt
    assert "ROUTE QUALITY RULES" in prompt


def test_route_rules_included_for_windows_style_route_path():
    # _sanitize_path / os-level paths can use backslashes on this platform --
    # the route-file check must not silently miss those.
    prompt = build_fixer_prompt(
        "app\\routes\\user_routes.py",
        "router = APIRouter()\n",
        ["Missing symbol 'user_router'"],
    )
    assert "SYMBOL REPAIR RULES" in prompt


def test_shared_rules_present_regardless_of_file_type():
    # PARAMETER ORDER RULES, DEPENDENCY REPAIR RULES, REACT RULES, and the
    # output-format rules are NOT route-specific (or are self-scoped by
    # their own "if repairing X" language) -- must still appear for every
    # file type, unaffected by this change.
    schema_prompt = build_fixer_prompt("app/schemas/user.py", "x", ["e"])
    route_prompt = build_fixer_prompt("app/routes/user_routes.py", "x", ["e"])
    for prompt in (schema_prompt, route_prompt):
        assert "PARAMETER ORDER RULES" in prompt
        assert "DEPENDENCY REPAIR RULES" in prompt
        assert "REACT RULES" in prompt
        assert "OUTPUT FORMAT" in prompt


def test_file_path_and_content_still_interpolated_correctly():
    prompt = build_fixer_prompt("app/schemas/user.py", "REAL_CONTENT_MARKER", ["ERROR_MARKER"])
    assert "app/schemas/user.py" in prompt
    assert "REAL_CONTENT_MARKER" in prompt
    assert "ERROR_MARKER" in prompt


if __name__ == "__main__":
    import traceback
    tests = [obj for name, obj in list(globals().items()) if name.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS: {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL: {t.__name__}: {e}")
        except Exception:
            failed += 1
            print(f"ERROR: {t.__name__}:")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    raise SystemExit(1 if failed else 0)
