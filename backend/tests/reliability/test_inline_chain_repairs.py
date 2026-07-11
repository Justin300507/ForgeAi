"""
Experiment 052: regression tests for the "inline chain" backend repair
functions in deterministic_patcher.py -- the ones operating on a raw
`content: str` (not `project_path: Path`), chained together per-file
inside run_deterministic_patches's per-.py-file loop, plus _patch_param_order,
patch_reorder_shadowed_static_routes, and _patch_router_names (project-wide
functions in the same "rewrites FastAPI/SQL code" category).

Real generated_projects/ output was checked first for fixtures (per the
task's "avoid toy examples" instruction) and came back empty for every
one of these patterns -- expected, since these patchers already ran
successfully on that output, so the broken pre-patch shape doesn't survive
into it. Fixtures below are built directly from each function's own regex/
logic (read from source, not guessed), which is the more reliable way to
exercise exact pattern boundaries than hunting for a real example that may
not hit every edge case anyway.

No repair logic was modified. Any behavior found ambiguous is documented,
not guessed at.

Run directly: python tests/reliability/test_inline_chain_repairs.py
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import app.services.deterministic_patcher as _dp
from app.services.deterministic_patcher import (
    _patch_wrong_auth_module,
    _patch_passlib,
    _patch_pydantic_regex,
    _patch_func_name_vs_label,
    _patch_smart_quotes,
    _patch_async_sync,
    _patch_circular_schema_imports,
    _patch_depends_body,
    _patch_from_orm,
    _patch_orm_response_model,
    _patch_param_order,
    _split_params,
    patch_reorder_shadowed_static_routes,
    _patch_router_names,
)


# ── _patch_wrong_auth_module ─────────────────────────────────────────────────

def test_wrong_auth_module_rewrites_to_app_utils_auth():
    src = "from app.utils.jwt_utils import create_access_token, decode_token\n"
    out = _patch_wrong_auth_module(src)
    assert out == "from app.utils.auth import create_access_token, decode_token\n"


def test_wrong_auth_module_covers_all_five_wrong_names():
    for wrong in ("jwt_utils", "jose", "security", "auth_utils", "jwt", "token_utils"):
        src = f"from app.utils.{wrong} import get_current_user\n"
        out = _patch_wrong_auth_module(src)
        assert out == "from app.utils.auth import get_current_user\n", wrong


def test_wrong_auth_module_noop_on_already_correct():
    src = "from app.utils.auth import get_current_user\n"
    assert _patch_wrong_auth_module(src) == src


def test_wrong_auth_module_idempotent():
    src = "from app.utils.jose import get_current_user\n"
    once = _patch_wrong_auth_module(src)
    twice = _patch_wrong_auth_module(once)
    assert once == twice


# ── _patch_passlib ────────────────────────────────────────────────────────────

_PASSLIB_SRC = '''\
from passlib.context import CryptContext
from fastapi import APIRouter

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)
'''


def test_passlib_strips_import_and_context_and_rewrites_calls():
    out = _patch_passlib(_PASSLIB_SRC)
    assert "passlib" not in out
    assert "CryptContext(" not in out
    assert "import bcrypt" in out
    assert "bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')" in out
    assert "bcrypt.checkpw(plain.encode('utf-8'), hashed.encode('utf-8'))" in out


def test_passlib_noop_when_absent():
    src = "import bcrypt\ndef f(): return bcrypt.gensalt()\n"
    assert _patch_passlib(src) == src


def test_passlib_idempotent():
    once = _patch_passlib(_PASSLIB_SRC)
    twice = _patch_passlib(once)
    assert once == twice
    assert "import bcrypt" in twice
    assert twice.count("import bcrypt") == 1, "must not stack a second bcrypt import on re-run"


def test_passlib_multiple_hash_calls_in_one_file():
    src = (
        "from passlib.context import CryptContext\n"
        "pwd_context = CryptContext(schemes=['bcrypt'])\n"
        "a = pwd_context.hash(x)\n"
        "b = pwd_context.hash(y)\n"
    )
    out = _patch_passlib(src)
    assert out.count("bcrypt.hashpw(") == 2


# ── _patch_pydantic_regex ────────────────────────────────────────────────────

def test_pydantic_regex_to_pattern():
    src = 'name: str = Field(regex=r"^[a-z]+$")\n'
    out = _patch_pydantic_regex(src)
    assert out == 'name: str = Field(pattern=r"^[a-z]+$")\n'


def test_pydantic_regex_noop_when_absent():
    src = 'name: str = Field(pattern=r"^[a-z]+$")\n'
    assert _patch_pydantic_regex(src) == src


def test_pydantic_regex_multiple_occurrences():
    src = 'a: str = Field(regex="^a$")\nb: str = Field(regex=\'^b$\')\n'
    out = _patch_pydantic_regex(src)
    assert "regex=" not in out
    assert out.count("pattern=") == 2


def test_pydantic_regex_idempotent():
    src = 'name: str = Field(regex="^x$")\n'
    once = _patch_pydantic_regex(src)
    twice = _patch_pydantic_regex(once)
    assert once == twice


# ── _patch_func_name_vs_label ────────────────────────────────────────────────

def test_func_name_vs_label_rewrites_chained_call():
    src = "stmt = select(func.count(User.id).name('total'))\n"
    out = _patch_func_name_vs_label(src)
    assert out == "stmt = select(func.count(User.id).label('total'))\n"


def test_func_name_vs_label_ignores_unrelated_dot_name():
    # A bare `.name(` not chained off a `func.xxx(...)` call must be left alone --
    # this function's whole point is distinguishing the two.
    src = "value = some_obj.name('literal call, not a func()')\n"
    out = _patch_func_name_vs_label(src)
    assert out == src


def test_func_name_vs_label_requires_both_substrings_present():
    # fast-skip guard: no "func." or no ".name(" at all -> untouched
    assert _patch_func_name_vs_label("x = 1\n") == "x = 1\n"
    assert _patch_func_name_vs_label("func.count(x)\n") == "func.count(x)\n"


def test_func_name_vs_label_handles_nested_parens_in_func_call():
    src = "stmt = select(func.coalesce(func.sum(Order.total), 0).name('sum'))\n"
    out = _patch_func_name_vs_label(src)
    # The OUTER func.coalesce(...) call is what .name( is chained onto here;
    # depth-tracking must walk past the nested func.sum(...) call correctly.
    assert ".label('sum')" in out
    assert ".name(" not in out


def test_func_name_vs_label_idempotent():
    src = "stmt = select(func.count(User.id).name('total'))\n"
    once = _patch_func_name_vs_label(src)
    twice = _patch_func_name_vs_label(once)
    assert once == twice


# ── _patch_smart_quotes ──────────────────────────────────────────────────────

def test_smart_quotes_translates_all_mapped_chars():
    src = "‘a’ “b” x – y — z"
    out = _patch_smart_quotes(src)
    assert out == "'a' \"b\" x - y - z"


def test_smart_quotes_noop_on_plain_ascii():
    src = "'a' \"b\" x - y - z"
    assert _patch_smart_quotes(src) == src


def test_smart_quotes_idempotent():
    src = "‘hello’"
    once = _patch_smart_quotes(src)
    twice = _patch_smart_quotes(once)
    assert once == twice == "'hello'"


# ── _patch_async_sync ─────────────────────────────────────────────────────────

def test_async_sync_strips_async_in_route_file_without_await():
    src = (
        "@task_router.get('/tasks')\n"
        "async def list_tasks(db: Session = Depends(get_db)):\n"
        "    return db.query(Task).all()\n"
    )
    out = _patch_async_sync(src, filepath="app/routes/task_routes.py")
    assert "async def list_tasks" not in out
    assert "def list_tasks" in out


def test_async_sync_leaves_real_await_alone_in_route_file():
    src = (
        "@task_router.get('/tasks')\n"
        "async def list_tasks():\n"
        "    result = await some_async_call()\n"
        "    return result\n"
    )
    out = _patch_async_sync(src, filepath="app/routes/task_routes.py")
    assert "async def list_tasks" in out


def test_async_sync_noop_when_no_async_def():
    src = "def list_tasks(db: Session):\n    return db.query(Task).all()\n"
    assert _patch_async_sync(src, filepath="app/routes/task_routes.py") == src


def test_async_sync_conservative_outside_route_files():
    # Outside route files, only strips when has_sync_orm / db-Depends / is a
    # route-decorated handler without await -- a plain async helper with sync
    # ORM calls (has_sync_orm True) still gets stripped; one with neither
    # sync ORM calls nor a Depends(Session) param and no decorator is left alone.
    src_untouched = "async def compute():\n    return 1 + 1\n"
    assert _patch_async_sync(src_untouched, filepath="app/services/math_service.py") == src_untouched

    src_stripped = (
        "async def save_thing(db: Session):\n"
        "    db.add(Thing())\n"
        "    db.commit()\n"
    )
    out = _patch_async_sync(src_stripped, filepath="app/services/thing_service.py")
    assert "async def save_thing" not in out


def test_async_sync_multiple_handlers_in_one_file():
    src = (
        "@x_router.get('/a')\n"
        "async def a(db: Session = Depends(get_db)):\n"
        "    return db.query(A).all()\n\n"
        "@x_router.get('/b')\n"
        "async def b(db: Session = Depends(get_db)):\n"
        "    return db.query(B).all()\n"
    )
    out = _patch_async_sync(src, filepath="app/routes/x_routes.py")
    assert out.count("async def") == 0
    assert out.count("def a(") == 1 and out.count("def b(") == 1


def test_async_sync_idempotent():
    src = (
        "@task_router.get('/tasks')\n"
        "async def list_tasks(db: Session = Depends(get_db)):\n"
        "    return db.query(Task).all()\n"
    )
    once = _patch_async_sync(src, filepath="app/routes/task_routes.py")
    twice = _patch_async_sync(once, filepath="app/routes/task_routes.py")
    assert once == twice


# ── _patch_circular_schema_imports ───────────────────────────────────────────

def test_circular_schema_imports_strips_route_import_in_schema_file():
    src = "from app.routes.task_routes import something\nfrom pydantic import BaseModel\n"
    out = _patch_circular_schema_imports(src, filepath="app/schemas/task.py")
    assert "from app.routes." not in out
    assert "from pydantic import BaseModel" in out


def test_circular_schema_imports_noop_outside_schemas_dir():
    src = "from app.routes.task_routes import something\n"
    assert _patch_circular_schema_imports(src, filepath="app/routes/other_routes.py") == src


def test_circular_schema_imports_noop_when_no_route_import():
    src = "from pydantic import BaseModel\n"
    assert _patch_circular_schema_imports(src, filepath="app/schemas/task.py") == src


def test_circular_schema_imports_idempotent():
    src = "from app.routes.task_routes import something\nx = 1\n"
    once = _patch_circular_schema_imports(src, filepath="app/schemas/task.py")
    twice = _patch_circular_schema_imports(once, filepath="app/schemas/task.py")
    assert once == twice


# ── _patch_depends_body ──────────────────────────────────────────────────────

def test_depends_body_strips_depends_on_schema_param():
    src = "def create_task(task_in: TaskCreate = Depends()):\n    pass\n"
    out = _patch_depends_body(src, filepath="app/routes/task_routes.py")
    assert out == "def create_task(task_in: TaskCreate):\n    pass\n"


def test_depends_body_requires_routes_dir():
    src = "def create_task(task_in: TaskCreate = Depends()):\n    pass\n"
    assert _patch_depends_body(src, filepath="app/services/task_service.py") == src


def test_depends_body_noop_without_empty_depends():
    src = "def create_task(db: Session = Depends(get_db)):\n    pass\n"
    assert _patch_depends_body(src, filepath="app/routes/task_routes.py") == src


def test_depends_body_idempotent():
    src = "def create_task(task_in: TaskCreate = Depends()):\n    pass\n"
    once = _patch_depends_body(src, filepath="app/routes/task_routes.py")
    twice = _patch_depends_body(once, filepath="app/routes/task_routes.py")
    assert once == twice


# ── _patch_from_orm ───────────────────────────────────────────────────────────

def test_from_orm_rewrites_to_model_validate():
    src = "return TaskResponse.from_orm(task)\n"
    out = _patch_from_orm(src)
    assert out == "return TaskResponse.model_validate(task, from_attributes=True)\n"


def test_from_orm_noop_when_absent():
    src = "return TaskResponse.model_validate(task, from_attributes=True)\n"
    assert _patch_from_orm(src) == src


def test_from_orm_multiple_occurrences():
    src = "a = A.from_orm(x)\nb = B.from_orm(y)\n"
    out = _patch_from_orm(src)
    assert out.count("model_validate(") == 2
    assert "from_orm" not in out


def test_from_orm_idempotent():
    src = "return TaskResponse.from_orm(task)\n"
    once = _patch_from_orm(src)
    twice = _patch_from_orm(once)
    assert once == twice


# ── _patch_orm_response_model ────────────────────────────────────────────────

_ORM_RESPONSE_SRC = '''\
from app.models.task import Task
from fastapi import APIRouter

task_router = APIRouter()

@task_router.get("/tasks/{id}", response_model=Task)
def get_task(id: int) -> Task:
    ...
'''

_ORM_RESPONSE_SCHEMA = '''\
from pydantic import BaseModel

class TaskResponse(BaseModel):
    id: int
'''


def test_orm_response_model_rewrites_response_model_and_return_type():
    with tempfile.TemporaryDirectory() as td:
        project = Path(td)
        (project / "app" / "schemas").mkdir(parents=True)
        (project / "app" / "schemas" / "task.py").write_text(_ORM_RESPONSE_SCHEMA, encoding="utf-8")

        out = _patch_orm_response_model(
            _ORM_RESPONSE_SRC, filepath="app/routes/task_routes.py", project_path=project
        )
        assert "response_model=TaskResponse" in out
        assert "-> TaskResponse:" in out
        assert "from app.schemas.task import TaskResponse" in out


def test_orm_response_model_falls_back_to_dict_without_matching_schema():
    with tempfile.TemporaryDirectory() as td:
        project = Path(td)
        (project / "app" / "schemas").mkdir(parents=True)
        # no schema file at all -> no match for "Task"
        out = _patch_orm_response_model(
            _ORM_RESPONSE_SRC, filepath="app/routes/task_routes.py", project_path=project
        )
        assert "response_model=dict" in out
        assert "-> dict:" in out


def test_orm_response_model_noop_outside_routes_dir():
    with tempfile.TemporaryDirectory() as td:
        project = Path(td)
        out = _patch_orm_response_model(
            _ORM_RESPONSE_SRC, filepath="app/services/task_service.py", project_path=project
        )
        assert out == _ORM_RESPONSE_SRC


def test_orm_response_model_idempotent():
    with tempfile.TemporaryDirectory() as td:
        project = Path(td)
        (project / "app" / "schemas").mkdir(parents=True)
        (project / "app" / "schemas" / "task.py").write_text(_ORM_RESPONSE_SCHEMA, encoding="utf-8")
        once = _patch_orm_response_model(
            _ORM_RESPONSE_SRC, filepath="app/routes/task_routes.py", project_path=project
        )
        twice = _patch_orm_response_model(
            once, filepath="app/routes/task_routes.py", project_path=project
        )
        assert once == twice, "second pass must not re-substitute or duplicate the import"


# ── _patch_param_order ───────────────────────────────────────────────────────

def _write_route_file(tmp: Path, content: str) -> Path:
    routes = tmp / "app" / "routes"
    routes.mkdir(parents=True, exist_ok=True)
    f = routes / "task_routes.py"
    f.write_text(content, encoding="utf-8")
    return f


_BROKEN_PARAM_ORDER = '''\
from fastapi import APIRouter, Path, Depends

task_router = APIRouter()

@task_router.put("/tasks/{task_id}")
def update_task(task_id: int = Path(...), task_in: dict, db=Depends()):
    pass
'''


def test_param_order_fixes_broken_signature_and_makes_file_compile():
    with tempfile.TemporaryDirectory() as td:
        project = Path(td)
        rf = _write_route_file(project, _BROKEN_PARAM_ORDER)
        # Confirm the fixture is genuinely broken before the fix, per the
        # task's "every claim backed by real execution" rule. Exp052:
        # CONFIRMED BUG found here -- _patch_param_order's own detection
        # only matched Python <3.10's wording ("non-default argument
        # follows default argument"); this interpreter (3.14) raises
        # "parameter without a default follows parameter with a default"
        # instead, so the function never fired on ANY file, ever, before
        # being fixed to accept both. Asserting the actual current-runtime
        # message here, not the stale one.
        try:
            compile(_BROKEN_PARAM_ORDER, str(rf), "exec")
            assert False, "fixture must be a genuine SyntaxError before patching"
        except SyntaxError as e:
            assert "parameter without a default follows parameter with a default" in (e.msg or "")

        n = _patch_param_order(project)
        assert n == 1
        fixed = rf.read_text(encoding="utf-8")
        compile(fixed, str(rf), "exec")  # must not raise now


def test_param_order_fast_skip_on_already_compiling_file():
    # Documented fast-skip behavior: compile() succeeding means the file is
    # left byte-identical, even if its param order looks unusual.
    already_valid = (
        "from fastapi import APIRouter\n"
        "task_router = APIRouter()\n\n"
        "@task_router.get('/tasks')\n"
        "def list_tasks(limit: int = 10, offset: int = 0):\n"
        "    pass\n"
    )
    with tempfile.TemporaryDirectory() as td:
        project = Path(td)
        rf = _write_route_file(project, already_valid)
        n = _patch_param_order(project)
        assert n == 0
        assert rf.read_text(encoding="utf-8") == already_valid


def test_param_order_skips_unrelated_syntax_errors():
    # A SyntaxError that ISN'T "non-default argument follows default
    # argument" must be left alone -- this function only handles that one
    # specific error shape.
    unrelated_error = "def broken(:\n    pass\n"
    with tempfile.TemporaryDirectory() as td:
        project = Path(td)
        rf = _write_route_file(project, unrelated_error)
        n = _patch_param_order(project)
        assert n == 0
        assert rf.read_text(encoding="utf-8") == unrelated_error


def test_param_order_idempotent():
    with tempfile.TemporaryDirectory() as td:
        project = Path(td)
        rf = _write_route_file(project, _BROKEN_PARAM_ORDER)
        n1 = _patch_param_order(project)
        assert n1 == 1
        after_first = rf.read_text(encoding="utf-8")
        n2 = _patch_param_order(project)
        assert n2 == 0, "already-compiling output must fast-skip on the second pass"
        assert rf.read_text(encoding="utf-8") == after_first


# ── Exp054: _split_params bracket-tracking fix ────────────────────────────────
#
# CONFIRMED BUG (found via direct reproduction, not assumption): the original
# _split_params only tracked `(`/`)` depth. A comma inside a bracketed type
# hint (e.g. `Dict[str, int]`) was treated as a top-level param separator,
# corrupting `filters: Dict[str, int] = Query({})` into two bogus fragments.
# Fed through _reorder_sig, that corruption produced syntactically INVALID
# Python that _patch_param_order then wrote straight to disk with no
# validation -- turning a recoverable SyntaxError into unparseable garbage.
# Both the split bug and the missing write-time validation are fixed here.

def test_split_params_respects_bracket_type_hint_with_comma():
    sig = "item_id: int = Path(...), filters: Dict[str, int] = Query({})"
    assert _split_params(sig) == [
        "item_id: int = Path(...)",
        "filters: Dict[str, int] = Query({})",
    ]


def test_split_params_still_respects_nested_parens():
    # Guard against a regression in the other direction -- bracket tracking
    # must not break the paren-nesting case this function already handled.
    sig = "a: int, b: str = Depends(get_thing(x, y))"
    assert _split_params(sig) == [
        "a: int",
        "b: str = Depends(get_thing(x, y))",
    ]


_BROKEN_PARAM_ORDER_WITH_BRACKET_TYPE = '''\
from fastapi import APIRouter, Path, Query
from typing import Dict

task_router = APIRouter()

@task_router.get("/tasks")
def list_tasks(item_id: int = Path(...), filters: Dict[str, int] = Query({}), name: str):
    pass
'''


def test_param_order_fixes_broken_signature_with_bracket_type_hint():
    # End-to-end reproduction of the confirmed corruption case: before the
    # fix, this exact input got rewritten into invalid Python and written to
    # disk unconditionally. Now it must both compile AND fast-skip on rerun.
    with tempfile.TemporaryDirectory() as td:
        project = Path(td)
        rf = _write_route_file(project, _BROKEN_PARAM_ORDER_WITH_BRACKET_TYPE)
        n = _patch_param_order(project)
        assert n == 1
        fixed = rf.read_text(encoding="utf-8")
        compile(fixed, str(rf), "exec")  # must not raise
        assert "Dict[str, int]" in fixed, "bracketed type hint must survive intact, not be split"

        n2 = _patch_param_order(project)
        assert n2 == 0, "fixed output must compile cleanly and fast-skip on rerun"


def test_param_order_write_guard_skips_invalid_reorder():
    # Directly exercises the new write-time safety net (Exp054), independent
    # of whether any known input can still trigger _reorder_sig producing
    # invalid syntax post-fix: force it to return garbage and confirm
    # _patch_param_order refuses to write it.
    original_reorder_sig = _dp._reorder_sig
    try:
        _dp._reorder_sig = lambda content, open_p, close_p, indent: "def broken(:\n    pass\n"
        with tempfile.TemporaryDirectory() as td:
            project = Path(td)
            rf = _write_route_file(project, _BROKEN_PARAM_ORDER)
            n = _patch_param_order(project)
            assert n == 0, "a reorder that produces invalid syntax must not be counted as a fix"
            assert rf.read_text(encoding="utf-8") == _BROKEN_PARAM_ORDER, (
                "the file must be left completely unpatched, not partially corrupted"
            )
    finally:
        _dp._reorder_sig = original_reorder_sig


# ── patch_reorder_shadowed_static_routes ─────────────────────────────────────

_SHADOWED_ROUTE_SRC = '''\
from fastapi import APIRouter

habit_router = APIRouter()

@habit_router.get("/habits/{habit_id}")
def get_habit(habit_id: int):
    pass

@habit_router.get("/habits/streaks")
def get_streaks():
    pass
'''


def test_reorder_shadowed_routes_moves_static_route_before_parameterized():
    with tempfile.TemporaryDirectory() as td:
        project = Path(td)
        rf = _write_route_file(project, _SHADOWED_ROUTE_SRC)
        n = patch_reorder_shadowed_static_routes(str(project))
        assert n == 1
        out = rf.read_text(encoding="utf-8")
        streaks_pos = out.index('"/habits/streaks"')
        param_pos = out.index('"/habits/{habit_id}"')
        assert streaks_pos < param_pos, "the static route must now be registered first"


def test_reorder_shadowed_routes_noop_when_not_shadowed():
    unshadowed = (
        "from fastapi import APIRouter\n"
        "habit_router = APIRouter()\n\n"
        "@habit_router.get('/habits/streaks')\n"
        "def get_streaks():\n    pass\n\n"
        "@habit_router.get('/habits/{habit_id}')\n"
        "def get_habit(habit_id: int):\n    pass\n"
    )
    with tempfile.TemporaryDirectory() as td:
        project = Path(td)
        rf = _write_route_file(project, unshadowed)
        n = patch_reorder_shadowed_static_routes(str(project))
        assert n == 0
        assert rf.read_text(encoding="utf-8") == unshadowed


def test_reorder_shadowed_routes_ignores_different_http_methods():
    # A GET /habits/{id} and a POST /habits/streaks don't shadow each other
    # -- Starlette dispatches by method first.
    different_methods = (
        "from fastapi import APIRouter\n"
        "habit_router = APIRouter()\n\n"
        "@habit_router.get('/habits/{habit_id}')\n"
        "def get_habit(habit_id: int):\n    pass\n\n"
        "@habit_router.post('/habits/streaks')\n"
        "def create_streak():\n    pass\n"
    )
    with tempfile.TemporaryDirectory() as td:
        project = Path(td)
        rf = _write_route_file(project, different_methods)
        n = patch_reorder_shadowed_static_routes(str(project))
        assert n == 0


def test_reorder_shadowed_routes_idempotent():
    with tempfile.TemporaryDirectory() as td:
        project = Path(td)
        rf = _write_route_file(project, _SHADOWED_ROUTE_SRC)
        n1 = patch_reorder_shadowed_static_routes(str(project))
        assert n1 == 1
        after_first = rf.read_text(encoding="utf-8")
        n2 = patch_reorder_shadowed_static_routes(str(project))
        assert n2 == 0
        assert rf.read_text(encoding="utf-8") == after_first


# ── _patch_router_names ──────────────────────────────────────────────────────

def test_router_names_renames_bare_router_and_updates_main():
    with tempfile.TemporaryDirectory() as td:
        project = Path(td)
        routes = project / "app" / "routes"
        routes.mkdir(parents=True)
        (routes / "task_routes.py").write_text(
            "from fastapi import APIRouter\nrouter = APIRouter()\n\n"
            "@router.get('/tasks')\ndef list_tasks():\n    pass\n",
            encoding="utf-8",
        )
        (project / "app" / "main.py").write_text(
            "from app.routes.task_routes import router\n"
            "app.include_router(router)\n",
            encoding="utf-8",
        )
        n = _patch_router_names(project)
        assert n == 1
        route_out = (routes / "task_routes.py").read_text(encoding="utf-8")
        assert "task_router = APIRouter()" in route_out
        assert "@task_router.get" in route_out
        main_out = (project / "app" / "main.py").read_text(encoding="utf-8")
        assert "import task_router" in main_out
        assert "include_router(task_router)" in main_out
        assert " router" not in main_out.replace("task_router", "")


def test_router_names_noop_when_already_correct():
    with tempfile.TemporaryDirectory() as td:
        project = Path(td)
        routes = project / "app" / "routes"
        routes.mkdir(parents=True)
        content = "from fastapi import APIRouter\ntask_router = APIRouter()\n"
        (routes / "task_routes.py").write_text(content, encoding="utf-8")
        n = _patch_router_names(project)
        assert n == 0
        assert (routes / "task_routes.py").read_text(encoding="utf-8") == content


def test_router_names_handles_route_singular_suffix():
    with tempfile.TemporaryDirectory() as td:
        project = Path(td)
        routes = project / "app" / "routes"
        routes.mkdir(parents=True)
        (routes / "task_route.py").write_text(
            "from fastapi import APIRouter\nrouter = APIRouter()\n", encoding="utf-8"
        )
        n = _patch_router_names(project)
        assert n == 1
        out = (routes / "task_route.py").read_text(encoding="utf-8")
        assert "task_router = APIRouter()" in out


def test_router_names_idempotent():
    with tempfile.TemporaryDirectory() as td:
        project = Path(td)
        routes = project / "app" / "routes"
        routes.mkdir(parents=True)
        (routes / "task_routes.py").write_text(
            "from fastapi import APIRouter\nrouter = APIRouter()\n", encoding="utf-8"
        )
        n1 = _patch_router_names(project)
        assert n1 == 1
        after_first = (routes / "task_routes.py").read_text(encoding="utf-8")
        n2 = _patch_router_names(project)
        assert n2 == 0
        assert (routes / "task_routes.py").read_text(encoding="utf-8") == after_first


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
