"""
Exp108: `from app.models.users import *` against a missing module was
skipped by BOTH passes of _patch_redirect_missing_backend_imports (each
filtered out '*'), leaving a hard ModuleNotFoundError at startup.
Confirmed live: tiny_notes (model file user.py, star import of .users).

Run directly: python tests/reliability/test_exp108_star_import_redirect.py
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.services.deterministic_patcher import _patch_redirect_missing_backend_imports


def _project(files: dict) -> Path:
    root = Path(tempfile.mkdtemp(prefix="exp108_test_"))
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return root


def test_star_import_redirected_to_singular_sibling():
    root = _project({
        "app/main.py": "from app.models.users import *  # noqa: F401\n",
        "app/models/__init__.py": "",
        "app/models/user.py": "class User:\n    pass\n",
    })
    try:
        assert _patch_redirect_missing_backend_imports(root) == 1
        out = (root / "app/main.py").read_text(encoding="utf-8")
        assert "from app.models.user import *" in out
        assert "app.models.users" not in out
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_star_import_redirected_to_plural_sibling():
    root = _project({
        "app/main.py": "from app.models.note import *\n",
        "app/models/__init__.py": "",
        "app/models/notes.py": "class Note:\n    pass\n",
    })
    try:
        assert _patch_redirect_missing_backend_imports(root) == 1
        assert "from app.models.notes import *" in (root / "app/main.py").read_text(encoding="utf-8")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_star_import_of_existing_module_untouched():
    root = _project({
        "app/main.py": "from app.models.user import *\n",
        "app/models/__init__.py": "",
        "app/models/user.py": "class User:\n    pass\n",
    })
    try:
        _patch_redirect_missing_backend_imports(root)
        assert "from app.models.user import *" in (root / "app/main.py").read_text(encoding="utf-8")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_star_import_with_no_sibling_left_alone():
    root = _project({
        "app/main.py": "from app.models.gadgets import *\n",
        "app/models/__init__.py": "",
        "app/models/user.py": "class User:\n    pass\n",
    })
    try:
        _patch_redirect_missing_backend_imports(root)
        assert "from app.models.gadgets import *" in (root / "app/main.py").read_text(encoding="utf-8")
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
