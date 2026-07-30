"""
Exp148: `Real` (a SQL/SQLite column-type NAME, not a real `sqlalchemy`
class) generated as a Column type crashes the whole app at import time
with `ImportError: cannot import name 'Real' from 'sqlalchemy'`.

Confirmed live (rental_property_management_app, 2026-07-30): the fix
loop's own cached "fix" for this exact diagnostic never actually removed
the bad import, so it never converged across 5 repair attempts.

Run directly: python tests/reliability/test_exp148_hallucinated_sqlalchemy_types.py
"""
import ast
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.services.deterministic_patcher import _patch_hallucinated_sqlalchemy_types


def _project(files: dict) -> Path:
    root = Path(tempfile.mkdtemp(prefix="exp148_test_"))
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return root


def test_replaces_real_with_float_and_fixes_import():
    root = _project({
        "app/models/payment.py": (
            "import builtins\n"
            "from app.database import Base\n"
            "from sqlalchemy import Column, Integer, Real, Date, Text, ForeignKey\n"
            "class Payment(Base):\n"
            "    __tablename__ = 'payments'\n"
            "    payment_id = Column(Integer, primary_key=True, nullable=False)\n"
            "    amount = Column(Real, nullable=False)\n"
        ),
    })
    try:
        assert _patch_hallucinated_sqlalchemy_types(root) == 1
        out = (root / "app/models/payment.py").read_text(encoding="utf-8")
        ast.parse(out)
        assert "Real" not in out
        assert "amount = Column(Float, nullable=False)" in out
        assert "from sqlalchemy import Column, Integer, Float, Date, Text, ForeignKey" in out
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_dedupes_if_float_already_imported():
    root = _project({
        "app/models/payment.py": (
            "from sqlalchemy import Column, Integer, Real, Float\n"
            "amount = Column(Real, nullable=False)\n"
            "rate = Column(Float, nullable=False)\n"
        ),
    })
    try:
        _patch_hallucinated_sqlalchemy_types(root)
        out = (root / "app/models/payment.py").read_text(encoding="utf-8")
        ast.parse(out)
        assert "from sqlalchemy import Column, Integer, Float" in out
        assert "amount = Column(Float, nullable=False)" in out
        assert "rate = Column(Float, nullable=False)" in out
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_clean_project_untouched():
    root = _project({
        "app/models/tenant.py": (
            "from sqlalchemy import Column, Integer, Text\n"
            "class Tenant:\n"
            "    tenant_id = Column(Integer, primary_key=True)\n"
        ),
    })
    try:
        before = (root / "app/models/tenant.py").read_text(encoding="utf-8")
        assert _patch_hallucinated_sqlalchemy_types(root) == 0
        assert (root / "app/models/tenant.py").read_text(encoding="utf-8") == before
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
