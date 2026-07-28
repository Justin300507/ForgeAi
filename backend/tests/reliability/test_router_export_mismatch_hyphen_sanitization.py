"""
Regression test for _patch_router_export_mismatch's identifier sanitization.

Reproduced live (ForgeBench v1.0, hospital_management_system, 2026-07-28) --
the same app and resource name ("consultation note") that motivated
_patch_hyphenated_router_identifiers back on 2026-07-13, recurring for a
DIFFERENT reason this time: expected_router was derived directly from the
route file's name (`route_file.name.replace("_routes.py", "") + "_router"`)
with no hyphen sanitization. For app/routes/consultation-note_routes.py
that produced expected_router = "consultation-note_router" -- a hyphen is
never valid inside a Python identifier, so the alias line this patcher
writes ("consultation-note_router = consultation_note_router") is ITSELF a
SyntaxError ("cannot assign to expression here. Maybe you meant '=='?"),
compounding the original hyphenated-import SyntaxError instead of fixing
anything. The whole app failed to even import -- every dimension failed
at once, Forge Score 19.54/F.

Run directly: python tests/reliability/test_router_export_mismatch_hyphen_sanitization.py
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.services.deterministic_patcher import _patch_router_export_mismatch


def _proj(tmp_path: Path) -> Path:
    (tmp_path / "app" / "routes").mkdir(parents=True, exist_ok=True)
    return tmp_path


def test_hyphenated_route_filename_produces_valid_underscored_alias(tmp_path):
    p = _proj(Path(tmp_path))
    route = p / "app" / "routes" / "consultation-note_routes.py"
    route.write_text(
        "from fastapi import APIRouter\n\n"
        "consultation_note_router_v2 = APIRouter()\n\n"
        "@consultation_note_router_v2.get(\"/consultation-notes\")\n"
        "def list_notes():\n"
        "    return []\n",
        encoding="utf-8",
    )
    n = _patch_router_export_mismatch(p)
    assert n == 1
    out = route.read_text(encoding="utf-8")
    # The written alias line must be a syntactically valid Python assignment --
    # no hyphen anywhere in the assigned-to name.
    alias_line = next(l for l in out.splitlines() if l.strip().endswith("_router = consultation_note_router_v2"))
    assigned_name = alias_line.split("=")[0].strip()
    assert "-" not in assigned_name
    assert assigned_name == "consultation_note_router"


def test_skips_when_correctly_named_router_already_exists(tmp_path):
    p = _proj(Path(tmp_path))
    route = p / "app" / "routes" / "consultation-note_routes.py"
    original = (
        "from fastapi import APIRouter\n\n"
        "consultation_note_router = APIRouter()\n\n"
        "@consultation_note_router.get(\"/consultation-notes\")\n"
        "def list_notes():\n"
        "    return []\n"
    )
    route.write_text(original, encoding="utf-8")
    n = _patch_router_export_mismatch(p)
    assert n == 0
    assert route.read_text(encoding="utf-8") == original


def test_non_hyphenated_filename_still_works(tmp_path):
    p = _proj(Path(tmp_path))
    route = p / "app" / "routes" / "habit_routes.py"
    route.write_text(
        "from fastapi import APIRouter\n\nrouter = APIRouter()\n",
        encoding="utf-8",
    )
    n = _patch_router_export_mismatch(p)
    assert n == 1
    out = route.read_text(encoding="utf-8")
    assert "habit_router = router" in out


if __name__ == "__main__":
    import traceback
    tests = [obj for name, obj in list(globals().items()) if name.startswith("test_")]
    failed = 0
    for t in tests:
        tmp = tempfile.mkdtemp(prefix="router_export_test_")
        try:
            t(tmp)
            print(f"PASS: {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL: {t.__name__}: {e}")
        except Exception:
            failed += 1
            print(f"ERROR: {t.__name__}:")
            traceback.print_exc()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    raise SystemExit(1 if failed else 0)
