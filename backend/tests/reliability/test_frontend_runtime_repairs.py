"""Regression coverage for generated SPA local-development routing."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.services.deterministic_patcher import _patch_vite_root_proxy_and_api_base


def test_root_proxy_is_removed_and_api_gets_a_local_fallback() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "src").mkdir()
        (root / "vite.config.js").write_text(
            "import { defineConfig } from 'vite'\n"
            "export default defineConfig({ server: { proxy: {\n"
            "'^/(?!src|@|node_modules|favicon|assets|index\\.html)': { target: 'http://localhost:8000' }\n"
            "} } })\n",
            encoding="utf-8",
        )
        api = root / "src" / "api.js"
        api.write_text("const API = axios.create({ baseURL: import.meta.env.VITE_API_URL, });\n", encoding="utf-8")

        assert _patch_vite_root_proxy_and_api_base(root) == 2
        assert "proxy" not in (root / "vite.config.js").read_text(encoding="utf-8")
        assert "VITE_API_URL || 'http://localhost:8000'" in api.read_text(encoding="utf-8")


if __name__ == "__main__":
    test_root_proxy_is_removed_and_api_gets_a_local_fallback()
    print("1/1 frontend runtime repair test passed")
