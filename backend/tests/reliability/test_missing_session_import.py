"""
Regression tests for _patch_missing_session_import_in_routes
(app/services/deterministic_patcher.py).

Root cause (habit_tracker, 2026-07-25): an "ARCHITECTURE REPAIR" LLM
response for some_endpoint_routes.py wrote
`def some_endpoint(db: Session = Depends(get_db))` while importing only
`from fastapi import APIRouter, Depends, Query, HTTPException` and
`from app.database import get_db` -- no `from sqlalchemy.orm import
Session` anywhere. `Session` is "just a type hint," so the omission
doesn't raise until Python evaluates the `def` statement at import time --
a hard NameError crash that takes the whole app down (every request
fails, not just one route), reproduced live via `validate_runtime`.

Run directly: python tests/reliability/test_missing_session_import.py
"""
import ast
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.services.deterministic_patcher import (
    _patch_missing_session_import_in_routes as _patch,
)


def _make_project(files: dict) -> Path:
    root = Path(tempfile.mkdtemp(prefix="sessionimport_"))
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return root


def test_injects_import_for_exact_live_shape():
    route = '''\
from fastapi import APIRouter, Depends, Query, HTTPException
from app.database import get_db

some_endpoint_router = APIRouter()

@some_endpoint_router.get("/some-endpoint")
def some_endpoint(db: Session = Depends(get_db)):
    return {"message": "This is a placeholder for some endpoint."}
'''
    root = _make_project({"app/routes/some_endpoint_routes.py": route})
    n = _patch(root)
    out = (root / "app" / "routes" / "some_endpoint_routes.py").read_text(encoding="utf-8")
    assert n == 1
    ast.parse(out)
    assert "from sqlalchemy.orm import Session" in out


def test_noop_when_already_imported():
    route = '''\
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.database import get_db

router = APIRouter()

@router.get("/x")
def x(db: Session = Depends(get_db)):
    return {}
'''
    root = _make_project({"app/routes/x_routes.py": route})
    n = _patch(root)
    out = (root / "app" / "routes" / "x_routes.py").read_text(encoding="utf-8")
    assert n == 0
    assert out == route


def test_noop_when_no_session_usage():
    route = '''\
from fastapi import APIRouter

router = APIRouter()

@router.get("/x")
def x():
    return {}
'''
    root = _make_project({"app/routes/x_routes.py": route})
    n = _patch(root)
    out = (root / "app" / "routes" / "x_routes.py").read_text(encoding="utf-8")
    assert n == 0
    assert out == route


def test_multiline_import_block_insertion_point_correct():
    route = '''\
from fastapi import (
    APIRouter,
    Depends,
)
from app.database import get_db

router = APIRouter()

@router.get("/x")
def x(db: Session = Depends(get_db)):
    return {}
'''
    root = _make_project({"app/routes/x_routes.py": route})
    n = _patch(root)
    out = (root / "app" / "routes" / "x_routes.py").read_text(encoding="utf-8")
    assert n == 1
    ast.parse(out)
    assert "from sqlalchemy.orm import Session" in out


def test_malformed_route_file_does_not_crash():
    root = _make_project({
        "app/routes/broken.py": "def f(db: Session:\n    not python\n",
        "app/routes/ok.py": (
            "from fastapi import APIRouter, Depends\n"
            "from app.database import get_db\n\n"
            "router = APIRouter()\n\n"
            "@router.get(\"/x\")\n"
            "def x(db: Session = Depends(get_db)):\n"
            "    return {}\n"
        ),
    })
    n = _patch(root)
    ok_out = (root / "app" / "routes" / "ok.py").read_text(encoding="utf-8")
    assert n == 1
    assert "from sqlalchemy.orm import Session" in ok_out


def test_idempotent_second_pass_is_noop():
    route = '''\
from fastapi import APIRouter, Depends
from app.database import get_db

router = APIRouter()

@router.get("/x")
def x(db: Session = Depends(get_db)):
    return {}
'''
    root = _make_project({"app/routes/x_routes.py": route})
    _patch(root)
    first = (root / "app" / "routes" / "x_routes.py").read_text(encoding="utf-8")
    n2 = _patch(root)
    second = (root / "app" / "routes" / "x_routes.py").read_text(encoding="utf-8")
    assert n2 == 0
    assert first == second


def test_no_op_when_no_routes_dir():
    root = Path(tempfile.mkdtemp(prefix="sessionimport_"))
    assert _patch(root) == 0


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
