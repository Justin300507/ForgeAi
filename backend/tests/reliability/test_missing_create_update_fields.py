"""
Verifies _patch_missing_create_update_fields (app/services/deterministic_patcher.py):
adds a field to a Create/Update Pydantic schema when a route handler reads it
off that schema's own parameter but it was never declared there.

Root cause this fixes (confirmed live, 2026-07-11, real generated output):
a gym-tracker app's WorkoutCreate schema only declared title/description,
but create_workout()'s handler did `date=workout_in.date` -- date was
correctly present on WorkoutResponse (and the model) but never carried
over to WorkoutCreate. AttributeError: 'WorkoutCreate' object has no
attribute 'date' on every single POST, 500ing the Create step of the CRUD
journey on every run.

Also verifies the per-function-scoping bug caught during self-review before
shipping: two functions sharing a parameter name (create_x(x_in: XCreate) /
update_x(x_in: XUpdate)) must not let the second function's type overwrite
the first's for attribute-access resolution.

Run directly: python tests/reliability/test_missing_create_update_fields.py
"""
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.services.deterministic_patcher import _patch_missing_create_update_fields


def _make_project(files: dict) -> Path:
    root = Path(tempfile.mkdtemp(prefix="missingfield_"))
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return root


def _run(root: Path):
    try:
        return _patch_missing_create_update_fields(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)


_SCHEMA = """from typing import Optional
from datetime import datetime
from pydantic import BaseModel, Field

class WorkoutCreate(BaseModel):
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)

class WorkoutUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None

class WorkoutResponse(BaseModel):
    id: int
    title: Optional[str] = None
    date: Optional[datetime] = None
"""

_ROUTES = """from app.schemas.workout import WorkoutCreate, WorkoutUpdate

def create_workout(workout_in: WorkoutCreate):
    return {"title": workout_in.title, "date": workout_in.date}

def update_workout(workout_in: WorkoutUpdate):
    if workout_in.date is not None:
        return workout_in.date
"""


def test_adds_corroborated_field_to_both_create_and_update():
    root = _make_project({
        "app/schemas/workout.py": _SCHEMA,
        "app/routes/workout_routes.py": _ROUTES,
    })
    n = _patch_missing_create_update_fields(root)
    content = (root / "app/schemas/workout.py").read_text(encoding="utf-8")
    shutil.rmtree(root, ignore_errors=True)
    assert n == 2  # counts per class patched (WorkoutCreate + WorkoutUpdate), same file
    assert "class WorkoutCreate" in content
    create_block = content[content.index("class WorkoutCreate"):content.index("class WorkoutUpdate")]
    update_block = content[content.index("class WorkoutUpdate"):content.index("class WorkoutResponse")]
    assert "date: Optional[Any] = None" in create_block
    assert "date: Optional[Any] = None" in update_block


def test_does_not_add_uncorroborated_field():
    """`notes` is read by the handler but declared on NO schema for this
    entity -- must NOT be guessed/added (that would silently paper over a
    possibly-genuine handler bug instead of surfacing it)."""
    root = _make_project({
        "app/schemas/workout.py": _SCHEMA,
        "app/routes/workout_routes.py": _ROUTES.replace(
            'return {"title": workout_in.title, "date": workout_in.date}',
            'return {"title": workout_in.title, "date": workout_in.date, "x": workout_in.notes}',
        ),
    })
    _patch_missing_create_update_fields(root)
    content = (root / "app/schemas/workout.py").read_text(encoding="utf-8")
    shutil.rmtree(root, ignore_errors=True)
    assert "notes" not in content


def test_per_function_scoping_shared_param_name():
    """The exact bug caught during self-review: create_workout and
    update_workout both name their parameter `workout_in` but with
    different types -- a file-wide (not per-function) param->class map
    would misattribute create_workout's accesses to WorkoutUpdate and
    silently skip patching WorkoutCreate at all."""
    root = _make_project({
        "app/schemas/workout.py": _SCHEMA,
        "app/routes/workout_routes.py": _ROUTES,
    })
    _patch_missing_create_update_fields(root)
    content = (root / "app/schemas/workout.py").read_text(encoding="utf-8")
    shutil.rmtree(root, ignore_errors=True)
    create_block = content[content.index("class WorkoutCreate"):content.index("class WorkoutUpdate")]
    assert "date" in create_block, (
        "WorkoutCreate must be patched independently of WorkoutUpdate even "
        "though both functions name their parameter `workout_in`"
    )


def test_pass_only_class_body_is_not_corrupted():
    """Regression test for a real bug caught during corpus validation: the
    original insertion logic tried to skip past a leading docstring/
    model_config line using a greedy `\\s*` that also ate into a
    `pass`-only body's indentation, leaving `pass` de-indented to column 0
    -- outside the class, invalid syntax. Confirmed against 9/50 real
    generated_projects/ before fixing (dine_reserve, forgeai_booking_platform,
    lean_sales_crm, sports_league_manager all had `pass`-only Update classes).
    """
    schema = (
        "from typing import Optional\n"
        "from pydantic import BaseModel\n\n"
        "class ContactBase(BaseModel):\n"
        "    email: Optional[str] = None\n\n"
        "class ContactUpdate(BaseModel):\n"
        "    pass\n\n"
        "class ContactResponse(ContactBase):\n"
        "    id: int\n"
        "    email: Optional[str] = None\n"
    )
    routes = (
        "from app.schemas.contact import ContactUpdate\n"
        "def update_contact(contact_in: ContactUpdate):\n"
        "    return contact_in.email\n"
    )
    root = _make_project({
        "app/schemas/contact.py": schema,
        "app/routes/contact_routes.py": routes,
    })
    _patch_missing_create_update_fields(root)
    content = (root / "app/schemas/contact.py").read_text(encoding="utf-8")
    shutil.rmtree(root, ignore_errors=True)
    import ast
    ast.parse(content)  # must not raise SyntaxError
    assert "    pass\n" in content  # pass must stay properly indented
    assert re.search(r"^pass$", content, re.MULTILINE) is None  # never de-indented to column 0


def test_prefix_collision_between_unrelated_entities_not_corroborated():
    """A field genuinely missing from TeamCreate must NOT be corroborated
    by an unrelated, longer-named entity that happens to share the prefix
    (TeamMemberResponse) -- only exact Team{Base,Create,Update,Response,...}
    siblings count."""
    schema = (
        "from typing import Optional\n"
        "from pydantic import BaseModel\n\n"
        "class TeamCreate(BaseModel):\n"
        "    name: str\n\n"
        "class TeamMemberResponse(BaseModel):\n"
        "    id: int\n"
        "    role: Optional[str] = None\n"
    )
    routes = (
        "from app.schemas.team import TeamCreate\n"
        "def create_team(team_in: TeamCreate):\n"
        "    return team_in.role\n"  # 'role' only exists on the UNRELATED TeamMemberResponse
    )
    root = _make_project({
        "app/schemas/team.py": schema,
        "app/routes/team_routes.py": routes,
    })
    _patch_missing_create_update_fields(root)
    content = (root / "app/schemas/team.py").read_text(encoding="utf-8")
    shutil.rmtree(root, ignore_errors=True)
    assert "role" not in content.split("class TeamMemberResponse")[0]


def test_already_defined_field_is_left_alone():
    root = _make_project({
        "app/schemas/workout.py": _SCHEMA,
        "app/routes/workout_routes.py":
            "from app.schemas.workout import WorkoutCreate\n"
            "def create_workout(workout_in: WorkoutCreate):\n"
            "    return workout_in.title\n",
    })
    n = _patch_missing_create_update_fields(root)
    shutil.rmtree(root, ignore_errors=True)
    assert n == 0


def test_no_schemas_dir_is_a_noop():
    root = _make_project({"app/routes/workout_routes.py": _ROUTES})
    assert _patch_missing_create_update_fields(root) == 0
    shutil.rmtree(root, ignore_errors=True)


# ── Model-column fallback (added after schema-only corroboration missed a
# real, confirmed bug -- see the function's own docstring for the Tag
# .name-vs-.title incident) ─────────────────────────────────────────────

_TAG_SCHEMA = (
    "from typing import Optional\n"
    "from pydantic import BaseModel, Field\n\n"
    "class TagCreate(BaseModel):\n"
    "    title: str = Field(min_length=1)\n\n"
    "class TagUpdate(BaseModel):\n"
    "    title: Optional[str] = None\n\n"
    "class TagResponse(BaseModel):\n"
    "    id: int\n"
    "    title: Optional[str] = None\n"
)
_TAG_MODEL = (
    "from sqlalchemy import Column, Integer, String\n"
    "from app.database import Base\n\n"
    "class Tag(Base):\n"
    "    __tablename__ = 'tags'\n"
    "    id = Column(Integer, primary_key=True)\n"
    "    name = Column(String, nullable=False)\n"
)
_TAG_ROUTES = (
    "from app.schemas.tag import TagCreate\n"
    "def create_tag(tag_in: TagCreate):\n"
    "    return Tag(name=tag_in.name)\n"
)


def test_falls_back_to_model_column_when_no_sibling_schema_corroborates():
    """The real incident: every schema for Tag agrees with every OTHER
    schema (all use `title`) -- schema-only corroboration would never fire.
    Only the model (and the route handler that actually uses it) knows
    about `name`."""
    root = _make_project({
        "app/schemas/tag.py": _TAG_SCHEMA,
        "app/models/tag.py": _TAG_MODEL,
        "app/routes/tag_routes.py": _TAG_ROUTES,
    })
    n = _patch_missing_create_update_fields(root)
    content = (root / "app/schemas/tag.py").read_text(encoding="utf-8")
    shutil.rmtree(root, ignore_errors=True)
    assert n == 1
    assert "name: Optional[Any] = None" in content.split("class TagCreate")[1].split("class TagUpdate")[0]


def test_model_fallback_tolerates_plural_model_class_name():
    root = _make_project({
        "app/schemas/tag.py": _TAG_SCHEMA,
        "app/models/tags.py": _TAG_MODEL.replace("class Tag(Base):", "class Tags(Base):"),
        "app/routes/tag_routes.py": _TAG_ROUTES,
    })
    n = _patch_missing_create_update_fields(root)
    shutil.rmtree(root, ignore_errors=True)
    assert n == 1


def test_no_model_fallback_without_any_matching_model_or_schema_evidence():
    """No Tag model at all (or an unmatched name) -- must NOT guess; a
    genuine route-handler typo/hallucination should surface, not be
    silently papered over."""
    root = _make_project({
        "app/schemas/tag.py": _TAG_SCHEMA,
        "app/routes/tag_routes.py": _TAG_ROUTES,
    })
    n = _patch_missing_create_update_fields(root)
    shutil.rmtree(root, ignore_errors=True)
    assert n == 0


def test_model_fallback_does_not_add_field_the_model_also_lacks():
    """A field neither any sibling schema NOR the model has (genuine typo)
    must stay unfixed by either corroboration path."""
    root = _make_project({
        "app/schemas/tag.py": _TAG_SCHEMA,
        "app/models/tag.py": _TAG_MODEL,
        "app/routes/tag_routes.py":
            "from app.schemas.tag import TagCreate\n"
            "def create_tag(tag_in: TagCreate):\n"
            "    return Tag(totally_made_up_field=tag_in.totally_made_up_field)\n",
    })
    n = _patch_missing_create_update_fields(root)
    shutil.rmtree(root, ignore_errors=True)
    assert n == 0


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
