"""Static navigation contracts must match the router, not merely page names."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.services.validator_service import validate_frontend_nav_targets


def test_similarly_named_page_does_not_mask_an_unrouted_link() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        pages = root / "src" / "pages"
        pages.mkdir(parents=True)
        (root / "src" / "App.jsx").write_text(
            '<Route path="/progress-view" element={<ProgressViewPage />} />\n',
            encoding="utf-8",
        )
        (pages / "ProgressViewPage.jsx").write_text("export default () => null;\n", encoding="utf-8")
        (root / "src" / "Sidebar.jsx").write_text('<Link to="/progress">Progress</Link>\n', encoding="utf-8")
        errors: list[str] = []

        validate_frontend_nav_targets(str(root), errors)

        assert "Missing frontend import target: ./pages/ProgressPage" in errors


if __name__ == "__main__":
    test_similarly_named_page_does_not_mask_an_unrouted_link()
    print("1/1 frontend navigation contract test passed")
