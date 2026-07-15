"""
Exp111: response_model=XBase/XCreate/XUpdate strips id (and other
server-generated fields) from every response when an XResponse exists.

Confirmed live (simple_crm, exp109-milestone-r2): every contact route
used ContactBase (no id) while ContactResponse (id: int) sat unused —
journey logged `Create entity: 201 id=None` and Edit/Delete cascade-
failed. Behavioral proof on the patched real app: Create returns
{"id": 1, ...}. Corpus prevalence: 19 occurrences / 5 apps.

Run directly: python tests/reliability/test_exp111_response_model_swap.py
"""
import ast
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.services.deterministic_patcher import _patch_wrong_schema_class_as_response_model


def _project(files: dict) -> Path:
    root = Path(tempfile.mkdtemp(prefix="exp111_test_"))
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return root


_SCHEMA = (
    "from pydantic import BaseModel\n\n"
    "class ContactBase(BaseModel):\n    name: str\n\n"
    "class ContactCreate(ContactBase):\n    pass\n\n"
    "class ContactResponse(ContactBase):\n    id: int\n"
)


def test_swaps_base_and_create_to_response_and_imports():
    root = _project({
        "app/schemas/contact.py": _SCHEMA,
        "app/routes/contact_routes.py": (
            "from fastapi import APIRouter\n"
            "from typing import List\n"
            "from app.schemas.contact import ContactBase, ContactCreate\n\n"
            "contact_router = APIRouter()\n\n"
            '@contact_router.get("/contacts", response_model=List[ContactBase])\n'
            "def list_contacts():\n    return []\n\n"
            '@contact_router.post("/contacts", response_model=ContactBase, status_code=201)\n'
            "def create_contact(c: ContactCreate):\n    return c\n"
        ),
    })
    try:
        assert _patch_wrong_schema_class_as_response_model(root) == 1
        out = (root / "app/routes/contact_routes.py").read_text(encoding="utf-8")
        ast.parse(out)
        assert "response_model=List[ContactResponse]" in out
        assert "response_model=ContactResponse" in out
        assert "from app.schemas.contact import ContactResponse" in out
        # the parameter annotation (a genuine Create usage) is untouched
        assert "def create_contact(c: ContactCreate):" in out
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_no_swap_when_no_better_class_exists():
    root = _project({
        "app/schemas/tag.py": (
            "from pydantic import BaseModel\n\nclass TagBase(BaseModel):\n    name: str\n"
        ),
        "app/routes/tag_routes.py": (
            "from fastapi import APIRouter\n"
            "from app.schemas.tag import TagBase\n\n"
            "tag_router = APIRouter()\n\n"
            '@tag_router.get("/tags", response_model=TagBase)\n'
            "def get_tag():\n    return {}\n"
        ),
    })
    try:
        assert _patch_wrong_schema_class_as_response_model(root) == 0
        assert "response_model=TagBase" in (root / "app/routes/tag_routes.py").read_text(encoding="utf-8")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_out_suffix_and_locally_defined_response_class():
    root = _project({
        "app/schemas/__init__.py": "",
        "app/routes/note_routes.py": (
            "from fastapi import APIRouter\n"
            "from pydantic import BaseModel\n\n"
            "class NoteCreate(BaseModel):\n    text: str\n\n"
            "class NoteOut(BaseModel):\n    id: int\n    text: str\n\n"
            "note_router = APIRouter()\n\n"
            '@note_router.post("/notes", response_model=NoteCreate)\n'
            "def create_note(n: NoteCreate):\n    return n\n"
        ),
    })
    try:
        assert _patch_wrong_schema_class_as_response_model(root) == 1
        out = (root / "app/routes/note_routes.py").read_text(encoding="utf-8")
        ast.parse(out)
        assert "response_model=NoteOut" in out
        # locally defined — no import added
        assert "from app.schemas" not in out
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
