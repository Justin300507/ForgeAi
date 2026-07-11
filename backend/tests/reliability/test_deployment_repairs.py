"""
Exp052: deterministic (non-LLM) repair functions for live-deployment
failures -- deployed_fixer.py (applied to an already-deployed app after a
health check fails) and deployment_fix_service.py (dispatched by parsed
deployment-error type). Both files also have an LLM-fallback function
(_llm_fix / generate_deployment_fix) which is explicitly out of scope --
not deterministic, not tested here.

Run directly: python tests/reliability/test_deployment_repairs.py
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.services.deployed_fixer import (
    _fix_cors, _fix_auth_utils, _fix_auth_routes,
    _fix_requirements as deployed_fix_requirements,
)
from app.services.deployment_fix_service import (
    _fix_health_check, _fix_port_error, _fix_frontend_build,
    _fix_cloudflare_build, _fix_render_timeout,
    _fix_requirements as deployment_fix_requirements,
)


def _tmp_project():
    d = Path(tempfile.mkdtemp(prefix="exp052_deploy_"))
    return d


def _cleanup(d):
    shutil.rmtree(d, ignore_errors=True)


# ── deployed_fixer.py ────────────────────────────────────────────────────────

def test_fix_cors_injects_middleware_when_missing():
    d = _tmp_project()
    try:
        main_py = d / "main.py"
        main_py.write_text(
            'from fastapi import FastAPI\napp = FastAPI()\n\n@app.get("/")\ndef root():\n    return {}\n',
            encoding="utf-8",
        )
        changed = _fix_cors(main_py)
        assert changed is True
        content = main_py.read_text(encoding="utf-8")
        assert "CORSMiddleware" in content
        assert 'allow_origins=["*"]' in content
    finally:
        _cleanup(d)


def test_fix_cors_noop_when_already_correct():
    d = _tmp_project()
    try:
        main_py = d / "main.py"
        original = (
            'from fastapi import FastAPI\n'
            'from fastapi.middleware.cors import CORSMiddleware\n'
            'app = FastAPI()\n'
            'app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, '
            'allow_methods=["*"], allow_headers=["*"])\n'
        )
        main_py.write_text(original, encoding="utf-8")
        changed = _fix_cors(main_py)
        assert changed is False
        assert main_py.read_text(encoding="utf-8") == original
    finally:
        _cleanup(d)


def test_fix_cors_missing_file_returns_false():
    d = _tmp_project()
    try:
        assert _fix_cors(d / "does_not_exist.py") is False
    finally:
        _cleanup(d)


def test_fix_cors_idempotent_across_two_calls():
    d = _tmp_project()
    try:
        main_py = d / "main.py"
        main_py.write_text('from fastapi import FastAPI\napp = FastAPI()\n', encoding="utf-8")
        _fix_cors(main_py)
        once = main_py.read_text(encoding="utf-8")
        second_change = _fix_cors(main_py)
        twice = main_py.read_text(encoding="utf-8")
        assert second_change is False
        assert once == twice
    finally:
        _cleanup(d)


def test_fix_auth_utils_writes_template_and_init():
    d = _tmp_project()
    try:
        result = _fix_auth_utils(d)
        assert result is True
        assert (d / "app" / "utils" / "auth.py").exists()
        assert (d / "app" / "utils" / "__init__.py").exists()
        content = (d / "app" / "utils" / "auth.py").read_text(encoding="utf-8")
        assert "get_current_user" in content
        assert "verify_password" in content
    finally:
        _cleanup(d)


def test_fix_auth_routes_writes_template_and_wires_main():
    d = _tmp_project()
    try:
        (d / "app").mkdir(parents=True)
        (d / "app" / "main.py").write_text(
            'from fastapi import FastAPI\napp = FastAPI()\n', encoding="utf-8"
        )
        result = _fix_auth_routes(d)
        assert result is True
        assert (d / "app" / "routes" / "auth_routes.py").exists()
        main_content = (d / "app" / "main.py").read_text(encoding="utf-8")
        assert "auth_router" in main_content
    finally:
        _cleanup(d)


def test_fix_auth_routes_noop_wiring_when_already_wired():
    d = _tmp_project()
    try:
        (d / "app").mkdir(parents=True)
        main_py = d / "app" / "main.py"
        main_py.write_text(
            'from fastapi import FastAPI\n'
            'from app.routes.auth_routes import auth_router\n'
            'app = FastAPI()\n'
            'app.include_router(auth_router)\n',
            encoding="utf-8",
        )
        _fix_auth_routes(d)
        content = main_py.read_text(encoding="utf-8")
        # must not have double-injected a second import/include line
        assert content.count("from app.routes.auth_routes import auth_router") == 1
        assert content.count("app.include_router(auth_router)") == 1
    finally:
        _cleanup(d)


def test_deployed_fix_requirements_drops_passlib_adds_pyjwt_bcrypt():
    d = _tmp_project()
    try:
        (d / "app").mkdir(parents=True)
        req = d / "app" / "requirements.txt"
        req.write_text("fastapi\npasslib[bcrypt]\npython-jose\nuvicorn\n", encoding="utf-8")
        result = deployed_fix_requirements(d)
        assert result is True
        content = req.read_text(encoding="utf-8")
        assert "passlib" not in content
        assert "python-jose" not in content
        assert "PyJWT" in content
        assert "bcrypt" in content
    finally:
        _cleanup(d)


def test_deployed_fix_requirements_noop_when_missing_file():
    d = _tmp_project()
    try:
        assert deployed_fix_requirements(d) is False
    finally:
        _cleanup(d)


def test_deployed_fix_requirements_idempotent():
    d = _tmp_project()
    try:
        (d / "app").mkdir(parents=True)
        req = d / "app" / "requirements.txt"
        req.write_text("fastapi\npasslib\n", encoding="utf-8")
        deployed_fix_requirements(d)
        once = req.read_text(encoding="utf-8")
        deployed_fix_requirements(d)
        twice = req.read_text(encoding="utf-8")
        assert once == twice


    finally:
        _cleanup(d)


# ── deployment_fix_service.py ────────────────────────────────────────────────

def test_fix_health_check_injects_endpoint():
    d = _tmp_project()
    try:
        (d / "app").mkdir(parents=True)
        (d / "app" / "main.py").write_text(
            'from fastapi import FastAPI\napp = FastAPI()\n', encoding="utf-8"
        )
        result = _fix_health_check(str(d), {})
        assert result is not None
        content = (d / "app" / "main.py").read_text(encoding="utf-8")
        assert '"/health"' in content


    finally:
        _cleanup(d)


def test_fix_health_check_noop_when_already_present():
    d = _tmp_project()
    try:
        (d / "app").mkdir(parents=True)
        (d / "app" / "main.py").write_text(
            'from fastapi import FastAPI\napp = FastAPI()\n\n@app.get("/health")\ndef h():\n    return {}\n',
            encoding="utf-8",
        )
        result = _fix_health_check(str(d), {})
        assert result is None


    finally:
        _cleanup(d)


def test_fix_health_check_missing_file_returns_none():
    d = _tmp_project()
    try:
        assert _fix_health_check(str(d), {}) is None
    finally:
        _cleanup(d)


def test_fix_port_error_is_a_deliberate_noop():
    # Documented in-source: the Dockerfile (not generated app code) handles
    # this, so the function always returns None -- locking in that contract.
    assert _fix_port_error("/anywhere", {}) is None


def test_fix_frontend_build_adds_missing_build_script_and_vite_config():
    d = _tmp_project()
    try:
        (d / "package.json").write_text(json.dumps({"name": "app", "scripts": {}}), encoding="utf-8")
        result = _fix_frontend_build(str(d), {})
        assert result is not None
        pkg = json.loads((d / "package.json").read_text(encoding="utf-8"))
        assert pkg["scripts"]["build"] == "vite build"
        assert "vite" in pkg["devDependencies"]
        assert (d / "vite.config.js").exists()


    finally:
        _cleanup(d)


def test_fix_frontend_build_noop_when_already_correct():
    d = _tmp_project()
    try:
        pkg = {
            "name": "app",
            "scripts": {"build": "vite build", "preview": "vite preview"},
            "devDependencies": {"vite": "^5.0.0", "@vitejs/plugin-react": "^4.0.0"},
        }
        (d / "package.json").write_text(json.dumps(pkg), encoding="utf-8")
        (d / "vite.config.js").write_text("export default {};\n", encoding="utf-8")
        result = _fix_frontend_build(str(d), {})
        assert result is None


    finally:
        _cleanup(d)


def test_fix_frontend_build_malformed_package_json_returns_none():
    d = _tmp_project()
    try:
        (d / "package.json").write_text("{not valid json", encoding="utf-8")
        assert _fix_frontend_build(str(d), {}) is None
    finally:
        _cleanup(d)


def test_fix_frontend_build_missing_package_json_returns_none():
    d = _tmp_project()
    try:
        assert _fix_frontend_build(str(d), {}) is None
    finally:
        _cleanup(d)


def test_fix_cloudflare_build_writes_headers_and_redirects():
    d = _tmp_project()
    try:
        (d / "package.json").write_text("{}", encoding="utf-8")
        result = _fix_cloudflare_build(str(d), {})
        assert (d / "public" / "_headers").exists()
        assert (d / "public" / "_redirects").exists()
        assert result is not None  # fix reported on the _redirects write


    finally:
        _cleanup(d)


def test_fix_cloudflare_build_noop_when_redirects_already_exists():
    d = _tmp_project()
    try:
        (d / "package.json").write_text("{}", encoding="utf-8")
        (d / "public").mkdir(parents=True)
        (d / "public" / "_redirects").write_text("/* /index.html 200\n", encoding="utf-8")
        result = _fix_cloudflare_build(str(d), {})
        assert result is None  # only the _redirects write reports a fix


    finally:
        _cleanup(d)


def test_fix_render_timeout_writes_render_yaml_with_port_binding():
    d = _tmp_project()
    try:
        result = _fix_render_timeout(str(d), {})
        assert result is not None
        content = (d / "render.yaml").read_text(encoding="utf-8")
        assert "$PORT" in content
        assert "healthCheckPath: /health" in content


    finally:
        _cleanup(d)


def test_fix_render_timeout_noop_when_already_configured():
    d = _tmp_project()
    try:
        (d / "render.yaml").write_text("services: []\n", encoding="utf-8")
        result = _fix_render_timeout(str(d), {})
        assert result is None


    finally:
        _cleanup(d)


def test_deployment_fix_requirements_corrects_package_name_casing():
    # This is a DIFFERENT function from deployed_fixer.py's _fix_requirements
    # (different file, different signature (str vs Path), different logic:
    # this one corrects casing/naming typos, not passlib->bcrypt swapping).
    d = _tmp_project()
    try:
        (d / "app").mkdir(parents=True)
        req = d / "app" / "requirements.txt"
        req.write_text("FastAPI\nPydantic\nUvicorn\nSQLAlchemy\n", encoding="utf-8")
        result = deployment_fix_requirements(str(d), {})
        assert result is not None
        content = req.read_text(encoding="utf-8")
        assert content == "fastapi\npydantic\nuvicorn[standard]\nsqlalchemy\n"


    finally:
        _cleanup(d)


def test_deployment_fix_requirements_noop_when_already_correct():
    d = _tmp_project()
    try:
        (d / "app").mkdir(parents=True)
        req = d / "app" / "requirements.txt"
        req.write_text("fastapi\npydantic\n", encoding="utf-8")
        result = deployment_fix_requirements(str(d), {})
        assert result is None


    finally:
        _cleanup(d)


def test_deployment_fix_requirements_missing_file_returns_none():
    d = _tmp_project()
    try:
        assert deployment_fix_requirements(str(d), {}) is None
    finally:
        _cleanup(d)


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
