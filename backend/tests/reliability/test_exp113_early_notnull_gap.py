"""
Exp113: fix_model_schema_notnull_gap ran ONLY in repair/preflight — i.e.
after the V6-stage validation loop had already crashed its journey on the
guaranteed NOT NULL IntegrityError and burned LLM runtime-fix calls on it.

Confirmed live (forge_blog_cms, exp112-milestone-r3 log): V6 runtime
validation journey crashed twice on posts.content_markdown (lines 290-338)
before the preflight relax finally fired at line 401. The fixer itself was
proven correct offline on the exact generated file; only its stage was
wrong. It now ALSO runs inside run_deterministic_patches, pre-validation.

Run directly: python tests/reliability/test_exp113_early_notnull_gap.py
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.services.deterministic_patcher import run_deterministic_patches


def test_notnull_gap_relaxed_by_early_deterministic_stage():
    root = Path(tempfile.mkdtemp(prefix="exp113_test_"))
    try:
        (root / "app" / "models").mkdir(parents=True)
        (root / "app" / "schemas").mkdir(parents=True)
        (root / "app" / "models" / "posts.py").write_text(
            "from sqlalchemy import Column, Integer, String, Text\n"
            "from app.database import Base\n\n"
            "class Post(Base):\n"
            "    __tablename__ = 'posts'\n"
            "    id = Column(Integer, primary_key=True)\n"
            "    title = Column(String(255), nullable=False)\n"
            "    content_markdown = Column(Text, nullable=False)\n",
            encoding="utf-8")
        (root / "app" / "schemas" / "post.py").write_text(
            "from pydantic import BaseModel\n\n"
            "class PostCreate(BaseModel):\n"
            "    title: str\n"
            "    content: str\n",
            encoding="utf-8")

        counts = run_deterministic_patches(str(root), skip_protected_injections=True)
        assert counts.get("fix_model_schema_notnull_gap_early"), counts
        out = (root / "app" / "models" / "posts.py").read_text(encoding="utf-8")
        # the un-satisfiable column is relaxed BEFORE any validation stage
        assert "content_markdown = Column(Text, nullable=True)" in out
        # a column covered by a required schema field stays strict
        assert "title = Column(String(255), nullable=False)" in out
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
