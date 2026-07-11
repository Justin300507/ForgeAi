"""
Verifies _patch_ownership_fk_attribute_drift (app/services/deterministic_patcher.py):
fixes ownership-FK naming drift in query/filter expressions, e.g.
`Contact.user_id` used in a filter when the model's real FK to users is
actually `Contact.owner_id`.

Root cause this fixes (confirmed live, 2026-07-11, lean_sales_crm): the
Contact model has BOTH a real FK `owner_id = Column(Integer,
ForeignKey("users.id"), nullable=False)` AND an unrelated, never-set
`user_id = Column(Integer, default=0, nullable=False)` that is NOT a
ForeignKey. contact_routes.py / deal_routes.py / stats_routes.py all
filter on `Contact.user_id == current_user.id`, which can never match any
real user (the column is always 0) -- every list/get/update/delete request
for a real user's own contacts silently returns nothing. A silent,
permanent data-isolation bug.

This is deliberately class-qualified (`ClassName.bad_attr`, never a bare
`.bad_attr`) and checks ForeignKey-typed columns specifically (not "any
column exists with this name") -- see _model_fk_columns, which fixed an
earlier version of this patcher that treated Contact.user_id (a real, but
non-FK, column) as "the model genuinely has it, not drift" and never fired.

Run directly: python tests/reliability/test_ownership_fk_drift.py
"""
import ast
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.services.deterministic_patcher import (
    _patch_ownership_fk_attribute_drift, _model_fk_columns,
)


def _make_project(files: dict) -> Path:
    root = Path(tempfile.mkdtemp(prefix="fkdrifttest_"))
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return root


_CONTACT_MODEL = '''\
from sqlalchemy import Column, Integer, String, ForeignKey
from app.database import Base

class Contact(Base):
    __tablename__ = "contacts"
    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    user_id = Column(Integer, default=0, nullable=False)
'''

_CONTACT_ROUTES = '''\
from fastapi import APIRouter, Depends
from app.models.contact import Contact

router = APIRouter()

@router.get("/contacts")
def list_contacts(db, current_user):
    query = db.query(Contact).filter(Contact.user_id == current_user.id)
    return query.all()

@router.get("/contacts/{id}")
def get_contact(id: int, db, current_user):
    contact = db.query(Contact).filter(Contact.id == id, Contact.user_id == current_user.id).first()
    return contact
'''


def test_fixes_class_qualified_drift_when_model_has_different_real_fk():
    root = _make_project({
        "app/models/contact.py": _CONTACT_MODEL,
        "app/routes/contact_routes.py": _CONTACT_ROUTES,
    })
    n = _patch_ownership_fk_attribute_drift(root)
    content = (root / "app/routes/contact_routes.py").read_text(encoding="utf-8")
    shutil.rmtree(root, ignore_errors=True)
    assert n == 1
    ast.parse(content)
    assert "Contact.user_id" not in content
    assert content.count("Contact.owner_id") == 2


def test_model_fk_columns_distinguishes_fk_from_plain_column():
    root = _make_project({"app/models/contact.py": _CONTACT_MODEL})
    fk_cols = _model_fk_columns(root / "app" / "models")
    shutil.rmtree(root, ignore_errors=True)
    assert fk_cols["Contact"] == {"owner_id"}
    assert "user_id" not in fk_cols["Contact"]


def test_does_not_touch_model_whose_real_fk_matches_the_name():
    """If the model's actual FK IS named user_id, that's not drift -- leave it."""
    model = '''\
from sqlalchemy import Column, Integer, ForeignKey
from app.database import Base

class Task(Base):
    __tablename__ = "tasks"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
'''
    routes = '''\
from app.models.task import Task

def list_tasks(db, current_user):
    return db.query(Task).filter(Task.user_id == current_user.id).all()
'''
    root = _make_project({
        "app/models/task.py": model,
        "app/routes/task_routes.py": routes,
    })
    n = _patch_ownership_fk_attribute_drift(root)
    content = (root / "app/routes/task_routes.py").read_text(encoding="utf-8")
    shutil.rmtree(root, ignore_errors=True)
    assert n == 0
    assert "Task.user_id" in content


def test_no_op_when_model_has_no_fk_columns_at_all():
    model = '''\
from sqlalchemy import Column, Integer, String
from app.database import Base

class Tag(Base):
    __tablename__ = "tags"
    id = Column(Integer, primary_key=True)
    name = Column(String)
'''
    root = _make_project({
        "app/models/tag.py": model,
        "app/routes/tag_routes.py": "from app.models.tag import Tag\nx = Tag.name\n",
    })
    n = _patch_ownership_fk_attribute_drift(root)
    shutil.rmtree(root, ignore_errors=True)
    assert n == 0


def test_class_qualified_does_not_touch_bare_attribute_access():
    """Only ClassName.bad_attr is rewritten -- a bare .user_id on some
    other, unresolved object (e.g. a dict or unrelated variable) must be
    left completely alone, unlike the blanket file-wide synonym patcher."""
    routes = '''\
from app.models.contact import Contact

def list_contacts(db, current_user):
    query = db.query(Contact).filter(Contact.user_id == current_user.id)
    payload = {"user_id": current_user.id}
    return query.all(), payload
'''
    root = _make_project({
        "app/models/contact.py": _CONTACT_MODEL,
        "app/routes/contact_routes.py": routes,
    })
    n = _patch_ownership_fk_attribute_drift(root)
    content = (root / "app/routes/contact_routes.py").read_text(encoding="utf-8")
    shutil.rmtree(root, ignore_errors=True)
    assert n == 1
    assert "Contact.owner_id" in content
    assert '"user_id": current_user.id' in content


def test_no_routes_or_models_dir_is_a_noop():
    root = Path(tempfile.mkdtemp(prefix="fkdrifttest_"))
    assert _patch_ownership_fk_attribute_drift(root) == 0
    shutil.rmtree(root, ignore_errors=True)


def test_multiple_models_in_same_file_each_evaluated_independently():
    """A route file referencing two models must not let one model's
    genuine FK name affect the other model's drift check."""
    models = '''\
from sqlalchemy import Column, Integer, ForeignKey
from app.database import Base

class Contact(Base):
    __tablename__ = "contacts"
    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    user_id = Column(Integer, default=0, nullable=False)

class Note(Base):
    __tablename__ = "notes"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
'''
    routes = '''\
from app.models.contact import Contact
from app.models.note import Note

def combined(db, current_user):
    contacts = db.query(Contact).filter(Contact.user_id == current_user.id).all()
    notes = db.query(Note).filter(Note.user_id == current_user.id).all()
    return contacts, notes
'''
    root = _make_project({
        "app/models/shared.py": models,
        "app/routes/combined_routes.py": routes,
    })
    n = _patch_ownership_fk_attribute_drift(root)
    content = (root / "app/routes/combined_routes.py").read_text(encoding="utf-8")
    shutil.rmtree(root, ignore_errors=True)
    assert n == 1
    ast.parse(content)
    assert "Contact.owner_id == current_user.id" in content
    assert "Note.user_id == current_user.id" in content


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
