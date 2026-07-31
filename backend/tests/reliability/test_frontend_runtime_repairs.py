"""Regression coverage for generated SPA local-development routing."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.services.deterministic_patcher import (
    _patch_vite_root_proxy_and_api_base,
    _patch_static_auth_header_to_interceptor,
)


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


_BUGGY_STATIC_AUTH_HEADER_API_JSX = (
    "import axios from 'axios';\n\n"
    "const API = axios.create({\n"
    "  baseURL: import.meta.env.VITE_API_URL || 'http://localhost:8000',\n"
    "  headers: {\n"
    "    'Content-Type': 'application/json',\n"
    "    Authorization: `Bearer ${localStorage.getItem('token')}`\n"
    "  }\n"
    "});\n\n"
    "API.interceptors.response.use(\n"
    "  response => response,\n"
    "  error => {\n"
    "    if (error.response.status === 401) {\n"
    "      localStorage.removeItem('token');\n"
    "      window.location.href = '/login';\n"
    "    }\n"
    "    return Promise.reject(error);\n"
    "  }\n"
    ");\n\n"
    "export default API;"
)


def test_static_auth_header_becomes_request_interceptor() -> None:
    """Regression test for a live habit_tracker bug (2026-08-01): a static
    `Authorization: Bearer ${localStorage.getItem('token')}` baked into
    axios.create()'s default headers is evaluated once at module import --
    typically before login -- and never re-evaluated after a no-reload SPA
    login updates localStorage. Every authenticated write request then 401s
    on a stale/missing token, and the (correctly generated) response
    interceptor's 401 handler bounces the user straight back to /login --
    "add task" appearing to instantly fail and redirect to login, exactly as
    reported live. Must rewrite to a request interceptor that reads the
    token fresh on every call, per the frontend prompt's own template."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "src").mkdir()
        api = root / "src" / "api.jsx"
        api.write_text(_BUGGY_STATIC_AUTH_HEADER_API_JSX, encoding="utf-8")

        assert _patch_static_auth_header_to_interceptor(root) == 1
        out = api.read_text(encoding="utf-8")

        assert "Authorization: `Bearer ${localStorage.getItem('token')}`" not in out
        assert "'Content-Type': 'application/json'," in out  # sibling header untouched
        assert "API.interceptors.request.use(cfg => {" in out
        assert "cfg.headers.Authorization = `Bearer ${token}`;" in out
        assert "API.interceptors.response.use(" in out  # existing 401 handler preserved
        assert out.count("{") == out.count("}")

        # Idempotent: a second pass must not duplicate the interceptor.
        assert _patch_static_auth_header_to_interceptor(root) == 0
        assert api.read_text(encoding="utf-8").count("interceptors.request.use") == 1


def test_static_auth_header_patch_is_noop_on_already_correct_client() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "src").mkdir()
        (root / "src" / "api.jsx").write_text(
            "import axios from 'axios';\n\n"
            "const API = axios.create({ baseURL: import.meta.env.VITE_API_URL || '' });\n"
            "API.interceptors.request.use(cfg => {\n"
            "  const token = localStorage.getItem('token');\n"
            "  if (token) cfg.headers.Authorization = `Bearer ${token}`;\n"
            "  return cfg;\n"
            "});\n\n"
            "export default API;",
            encoding="utf-8",
        )
        assert _patch_static_auth_header_to_interceptor(root) == 0


if __name__ == "__main__":
    test_root_proxy_is_removed_and_api_gets_a_local_fallback()
    test_static_auth_header_becomes_request_interceptor()
    test_static_auth_header_patch_is_noop_on_already_correct_client()
    print("3/3 frontend runtime repair tests passed")
