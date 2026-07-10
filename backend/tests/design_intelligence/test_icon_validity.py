"""
Icon-validity guardrail tests: every icon name ForgeAI can put into a
generated app must exist in the PINNED lucide-react version, and the
invalid-icon patcher must mechanically fix hallucinated names. Plain
assert-based -- run directly:
python tests/design_intelligence/test_icon_validity.py
"""
import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.knowledge.lucide_icon_exports import VALID_LUCIDE_ICONS
from app.prompts.design_system import CATEGORIES
from app.services.deterministic_patcher import (
    _LUCIDE_ICONS,
    _LUCIDE_INVALID_RENAMES,
    _patch_invalid_lucide_icons,
)


def test_design_vocab_icons_all_exist():
    for cat, ds in CATEGORIES.items():
        bad = [i for i in ds["icons"] if i not in VALID_LUCIDE_ICONS]
        assert not bad, f"{cat} vocabulary has non-existent icons: {bad}"


def test_component_snippets_reference_only_real_icons():
    from app.prompts.component_library import COMPONENTS
    for key, comp in COMPONENTS.items():
        for icon in re.findall(r"<(\w+) size=", comp["code"]):
            assert icon in VALID_LUCIDE_ICONS, f"{key} snippet uses non-existent icon {icon}"


def test_frontend_prompt_import_example_icons_exist():
    from app.prompts.frontend_prompt import build_frontend_prompt
    prompt = build_frontend_prompt({"project_name": "x", "features": [], "api_endpoints": []},
                                   idea="a todo app")
    m = re.search(r"import \{\{?([^}]*)\}?\} from 'lucide-react'", prompt)
    assert m, "icon import example missing from prompt"
    names = [n.strip() for n in m.group(1).replace("\n", " ").split(",") if n.strip()]
    bad = [n for n in names if n not in VALID_LUCIDE_ICONS]
    assert not bad, f"prompt import example has non-existent icons: {bad}"


def test_curated_whitelist_is_sanitized():
    bad = _LUCIDE_ICONS - VALID_LUCIDE_ICONS
    assert not bad, f"curated whitelist still contains non-exports: {sorted(bad)}"


def test_rename_targets_all_exist():
    bad = {k: v for k, v in _LUCIDE_INVALID_RENAMES.items() if v not in VALID_LUCIDE_ICONS}
    assert not bad, f"rename map points at non-existent icons: {bad}"
    assert "Circle" in VALID_LUCIDE_ICONS  # the generic fallback


def test_patcher_fixes_hallucinated_icons():
    """Reproduces the real m3-canary crm failure: 'Handshake' is not
    exported. The patcher must fix the import, the JSX usages, and leave
    valid icons alone."""
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "src" / "pages"
        src.mkdir(parents=True)
        f = src / "Deals.jsx"
        f.write_text(
            "import React from 'react';\n"
            "import { Handshake, Users, ChartBar, Madeupicon } from 'lucide-react';\n"
            "const Deals = () => (\n"
            "  <div>\n"
            "    <Handshake size={18} />\n"
            "    <Users size={18} />\n"
            "    <ChartBar size={18} />\n"
            "    <Madeupicon size={18} />\n"
            "  </div>\n"
            ");\n"
            "export default Deals;\n",
            encoding="utf-8",
        )
        n = _patch_invalid_lucide_icons(Path(tmp))
        out = f.read_text(encoding="utf-8")
        assert n == 1
        assert not re.search(r"\bHandshake\b", out) and "HeartHandshake" in out
        assert "ChartBar" not in out.replace("BarChart2", "") and "BarChart2" in out
        assert "Madeupicon" not in out and "Circle" in out  # generic fallback
        assert "<Users size={18} />" in out  # valid icon untouched
        imports = re.search(r"import \{([^}]*)\} from 'lucide-react'", out).group(1)
        names = [x.strip() for x in imports.split(",")]
        assert len(names) == len(set(names)), f"duplicate imports: {names}"
        for name in names:
            assert name in VALID_LUCIDE_ICONS, f"patched import still invalid: {name}"


def test_patcher_handles_aliases_and_collisions():
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "src"
        src.mkdir(parents=True)
        f = src / "App.jsx"
        f.write_text(
            "import { Grid3x3 as GridIcon, LayoutGrid, House } from 'lucide-react';\n"
            "const App = () => <div><GridIcon /><LayoutGrid /><House /></div>;\n"
            "export default App;\n",
            encoding="utf-8",
        )
        _patch_invalid_lucide_icons(Path(tmp))
        out = f.read_text(encoding="utf-8")
        assert "Grid3x3" not in out
        assert "LayoutGrid as GridIcon" in out  # alias preserved, source fixed
        assert "<GridIcon />" in out            # usage untouched for aliased import
        assert "House" not in out and "Home" in out
        imports = re.search(r"import \{([^}]*)\} from 'lucide-react'", out).group(1)
        names = [x.strip().split(" as ")[-1] for x in imports.split(",")]
        assert len(names) == len(set(names)), f"duplicate local names: {names}"


def test_patcher_noop_on_clean_file():
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "src"
        src.mkdir(parents=True)
        f = src / "Clean.jsx"
        original = (
            "import { Users, Calendar } from 'lucide-react';\n"
            "const C = () => <Users />;\nexport default C;\n"
        )
        f.write_text(original, encoding="utf-8")
        assert _patch_invalid_lucide_icons(Path(tmp)) == 0
        assert f.read_text(encoding="utf-8") == original


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  PASS {name}")
            except AssertionError as e:
                failures += 1
                print(f"  FAIL {name}: {e}")
    print(f"\n{'ALL PASS' if failures == 0 else f'{failures} FAILURE(S)'}")
    sys.exit(1 if failures else 0)
