"""
Exp110: db.refresh/add/delete on a name bound only inside a nested block →
UnboundLocalError 500 on empty input.

Confirmed live (forge_blog_cms create_post, exp109-milestone-r2 canary):
`association` is bound only inside `if post_in.tags:`'s loop, then
`db.refresh(association)` runs unconditionally — the journey's `tags: []`
payload 500'd Create and cascade-failed all CRUD steps across multiple
repair rounds. Behavioral proof: the patched copy returned 201 for the
exact failing payload.

Run directly: python tests/reliability/test_exp110_unbound_db_ops.py
"""
import ast
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.services.deterministic_patcher import _patch_unbound_conditional_db_ops


_LIVE_SHAPE = '''from fastapi import APIRouter
post_router = APIRouter()

@post_router.post("/posts")
def create_post(post_in, db):
    new_post = object()
    db.add(new_post)
    db.flush()
    if post_in.tags:
        for tag_name in post_in.tags:
            association = (tag_name,)
            db.add(association)
    db.commit()
    db.refresh(association)
    db.refresh(new_post)
    return new_post
'''


def _project(files: dict) -> Path:
    root = Path(tempfile.mkdtemp(prefix="exp110_test_"))
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return root


def test_guards_live_shape_and_still_parses():
    root = _project({"app/routes/post_routes.py": _LIVE_SHAPE})
    try:
        assert _patch_unbound_conditional_db_ops(root) == 1
        out = (root / "app/routes/post_routes.py").read_text(encoding="utf-8")
        ast.parse(out)
        assert "association = None" in out
        assert "if association is not None:" in out
        # exec-level proof: empty tags no longer raises
        class _DB:
            def add(self, x): pass
            def flush(self): pass
            def commit(self): pass
            def refresh(self, x): pass
        class _In:
            tags = []
        ns = {}
        exec(compile(out, "<patched>", "exec"), {"APIRouter": lambda: type("R", (), {"post": lambda *a, **k: (lambda f: f)})()}, ns)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_unconditionally_bound_target_untouched():
    src = (
        "def create(db):\n"
        "    item = object()\n"
        "    db.add(item)\n"
        "    db.commit()\n"
        "    db.refresh(item)\n"
    )
    root = _project({"app/routes/item_routes.py": src})
    try:
        assert _patch_unbound_conditional_db_ops(root) == 0
        assert (root / "app/routes/item_routes.py").read_text(encoding="utf-8") == src
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_parameter_named_target_untouched():
    src = (
        "def create(db, item):\n"
        "    if item:\n"
        "        item = transform(item)\n"
        "    db.refresh(item)\n"
    )
    root = _project({"app/routes/p_routes.py": src})
    try:
        assert _patch_unbound_conditional_db_ops(root) == 0
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_for_loop_bound_target_guarded():
    src = (
        "def seed(db, rows):\n"
        "    for new_todo in rows:\n"
        "        db.add(new_todo)\n"
        "    db.commit()\n"
        "    db.refresh(new_todo)\n"
    )
    root = _project({"app/routes/seed_routes.py": src})
    try:
        assert _patch_unbound_conditional_db_ops(root) == 1
        out = (root / "app/routes/seed_routes.py").read_text(encoding="utf-8")
        ast.parse(out)
        assert "new_todo = None" in out
        assert "if new_todo is not None:" in out
    finally:
        shutil.rmtree(root, ignore_errors=True)


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
