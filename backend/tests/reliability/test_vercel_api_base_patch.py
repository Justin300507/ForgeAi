"""
Root cause: VercelProvider.deploy() patches src/api.js's axios baseURL to the
relative '/api' path for same-origin deploys, but the regex only matched a
bare string literal (`baseURL: ''`). The frontend generation prompt actually
emits `baseURL: import.meta.env.VITE_API_URL || 'http://localhost:8000'`
(confirmed via a real generation response), which the old regex never
matched -- so the patch silently no-op'd and the live bundle shipped
targeting the visitor's own localhost, producing axios "Network Error" in
production.
"""
from app.deployments.vercel_provider import _patch_api_base_url


def test_patches_bare_empty_string_literal():
    text = "const API = axios.create({ baseURL: '' });"
    assert _patch_api_base_url(text) == "const API = axios.create({ baseURL: '/api' });"


def test_patches_import_meta_env_expression_with_empty_fallback():
    text = "const API = axios.create({ baseURL: import.meta.env.VITE_API_URL || '' });"
    assert _patch_api_base_url(text) == "const API = axios.create({ baseURL: '/api' });"


def test_patches_import_meta_env_expression_with_localhost_fallback():
    # This is the literal shape the frontend prompt template actually produces.
    text = "const API = axios.create({ baseURL: import.meta.env.VITE_API_URL || 'http://localhost:8000' });"
    assert _patch_api_base_url(text) == "const API = axios.create({ baseURL: '/api' });"


if __name__ == "__main__":
    test_patches_bare_empty_string_literal()
    test_patches_import_meta_env_expression_with_empty_fallback()
    test_patches_import_meta_env_expression_with_localhost_fallback()
    print("3/3 vercel api-base patch tests passed")
