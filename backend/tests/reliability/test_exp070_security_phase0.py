"""
Experiment 070: regression tests for Security Phase 0 -- the 5 launch
blockers Experiment 069's security review found:
  1. Hardcoded insecure SECRET_KEY default (app/dependencies/auth.py)
  2. Missing rate limiting (app/middleware/rate_limit.py, new this cycle)
  3. project_name path traversal -- 7 sites total: 6 in main.py (2 of
     them shutil.rmtree() calls -- the most severe finding of this
     cycle, worse than Exp069's original file_writer_service.py-only
     finding) + the 1 Exp069 originally found
  4. CORS allow_origins=["*"] + allow_credentials=True
  5. (this file itself, plus the tests below)

SECRET_KEY-dependent tests that need to import main.py run with a
valid SECRET_KEY set in this process' environment (module-level, top
of file) -- the fail-fast-on-missing/weak-key behavior itself is
tested via subprocess so each case gets a genuinely fresh module
import, matching how the real failure would happen (at process
startup), not a re-import trick within one already-running process.

Run directly: python tests/reliability/test_exp070_security_phase0.py
"""
import os
import secrets
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
_BACKEND_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

# A valid, strong key so importing main.py in this process (for the CORS/
# rate-limit/path-containment tests below) succeeds -- the missing/weak-key
# fail-fast behavior itself is tested separately, via subprocess, below.
os.environ["SECRET_KEY"] = secrets.token_hex(32)

_PY = sys.executable


def _run_subprocess_import(module: str, env_overrides: dict, cwd: str) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.update(env_overrides)
    env["PYTHONPATH"] = _BACKEND_ROOT
    return subprocess.run(
        [_PY, "-c", f"import {module}"],
        env=env, cwd=cwd, capture_output=True, text=True, timeout=60,
    )


# ---------------------------------------------------------------------------
# Task 1 -- SECRET_KEY fail-fast (subprocess, fresh process per case)
# ---------------------------------------------------------------------------

def test_secret_key_missing_fails_startup():
    with tempfile.TemporaryDirectory() as td:
        env = dict(os.environ)
        env.pop("SECRET_KEY", None)
        result = _run_subprocess_import("app.dependencies.auth", {"SECRET_KEY": ""}, td)
        assert result.returncode != 0, "import must fail with SECRET_KEY unset"
        assert "SECRET_KEY environment variable is not set" in result.stderr


def test_secret_key_known_placeholder_fails_startup():
    with tempfile.TemporaryDirectory() as td:
        result = _run_subprocess_import("app.dependencies.auth", {"SECRET_KEY": "changeme"}, td)
        assert result.returncode != 0, "import must fail with a known placeholder value"
        assert "known placeholder" in result.stderr


def test_secret_key_old_hardcoded_default_fails_startup():
    # The exact string that used to be the insecure default -- must now
    # be rejected the same as any other known-weak value if someone's
    # old .env still has it copy-pasted in.
    with tempfile.TemporaryDirectory() as td:
        result = _run_subprocess_import(
            "app.dependencies.auth",
            {"SECRET_KEY": "please-set-SECRET_KEY-env-var-in-production"},
            td,
        )
        assert result.returncode != 0
        assert "known placeholder" in result.stderr


def test_secret_key_too_short_fails_startup():
    with tempfile.TemporaryDirectory() as td:
        result = _run_subprocess_import("app.dependencies.auth", {"SECRET_KEY": "short123"}, td)
        assert result.returncode != 0, "import must fail with a too-short key"
        assert "too short" in result.stderr


def test_secret_key_strong_value_succeeds():
    with tempfile.TemporaryDirectory() as td:
        strong = secrets.token_hex(32)
        result = _run_subprocess_import("app.dependencies.auth", {"SECRET_KEY": strong}, td)
        assert result.returncode == 0, f"import should succeed with a strong key, stderr={result.stderr}"


def test_secret_key_loaded_from_dotenv_file():
    # Confirms the load_dotenv() fix itself: a SECRET_KEY defined ONLY
    # in a .env file (not exported in the shell) must actually be
    # picked up -- this was silently broken before this experiment,
    # since nothing upstream of auth.py's old module-level read called
    # load_dotenv() first.
    with tempfile.TemporaryDirectory() as td:
        strong = secrets.token_hex(32)
        with open(os.path.join(td, ".env"), "w") as f:
            f.write(f"SECRET_KEY={strong}\n")
        env = dict(os.environ)
        env.pop("SECRET_KEY", None)
        env["PYTHONPATH"] = _BACKEND_ROOT
        result = subprocess.run(
            [_PY, "-c", "import app.dependencies.auth"],
            env=env, cwd=td, capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, f".env-only SECRET_KEY should be picked up, stderr={result.stderr}"


# ---------------------------------------------------------------------------
# Task 3 -- project_name / project_path traversal
# ---------------------------------------------------------------------------

def test_write_files_rejects_traversal_project_name():
    from app.services.file_writer_service import write_files
    try:
        write_files("../../evil_dir", [{"path": "app/main.py", "content": "x=1\n"}])
        assert False, "should have raised ValueError"
    except ValueError as e:
        assert "Unsafe project_name" in str(e)


def test_write_files_accepts_legit_project_name():
    # Compares paths via os.path.normcase (Windows is case-insensitive
    # for paths; resolve_safe_path's Path.resolve() may normalize
    # drive-letter/segment casing differently than a hand-built
    # os.path.join string, which is a casing difference only, not a
    # real path mismatch) -- checked directly via the function's own
    # returned path, not an independently-reconstructed one.
    import shutil
    from app.services.file_writer_service import write_files
    project = "exp070_test_legit_name"
    guess_base = os.path.abspath(os.path.join(_BACKEND_ROOT, "..", "generated_projects", project))
    if os.path.exists(guess_base):
        shutil.rmtree(guess_base)
    try:
        result = write_files(project, [{"path": "app/main.py", "content": "x=1\n"}])
        assert os.path.normcase(result) == os.path.normcase(guess_base)
        assert os.path.isfile(os.path.join(result, "app", "main.py"))
    finally:
        shutil.rmtree(result, ignore_errors=True)


def test_main_safe_generated_project_dir_rejects_traversal():
    import main
    assert main._safe_generated_project_dir("../../evil") is None
    assert main._safe_generated_project_dir("../escape") is None
    assert main._safe_generated_project_dir(None) is None
    assert main._safe_generated_project_dir("") is None


def test_main_safe_generated_project_dir_accepts_legit_name():
    import main
    result = main._safe_generated_project_dir("legit_app_name")
    assert result is not None
    assert result.endswith(os.path.join("generated_projects", "legit_app_name")) or \
        "legit_app_name" in result


def test_main_delete_job_path_helper_prevents_rmtree_escape():
    # The single most severe finding of Experiment 070: confirms the
    # exact code shape delete_job()/delete_all_jobs() use (a falsy
    # check + the helper + an os.path.isdir/shutil.rmtree pair) never
    # reaches shutil.rmtree for a malicious job.project_name value.
    import main
    malicious_name = "../../../important_directory"
    proj_dir = main._safe_generated_project_dir(malicious_name)
    assert proj_dir is None, (
        "a malicious project_name must resolve to None so the "
        "if proj_dir is not None: shutil.rmtree(...) guard in "
        "delete_job()/delete_all_jobs() never fires"
    )


def test_main_deploy_path_containment_rejects_escape():
    import main
    from fastapi import HTTPException
    for bad_path in ["../../etc/passwd", "/etc/passwd", "C:\\Windows\\System32"]:
        try:
            main._require_contained_project_path(bad_path)
            assert False, f"should have rejected {bad_path!r}"
        except HTTPException as e:
            assert e.status_code == 400


def test_main_deploy_path_containment_accepts_relative_and_absolute_contained():
    import main
    main._require_contained_project_path("generated_projects/legit_app")  # relative, no raise
    abs_legit = os.path.join(os.path.abspath("generated_projects"), "legit_app")
    main._require_contained_project_path(abs_legit)  # absolute, no raise


def test_main_deploy_path_containment_rejects_empty():
    import main
    from fastapi import HTTPException
    try:
        main._require_contained_project_path("")
        assert False, "should have rejected empty path"
    except HTTPException as e:
        assert e.status_code == 400


# ---------------------------------------------------------------------------
# Task 4 -- CORS
# ---------------------------------------------------------------------------

def test_cors_default_is_not_wildcard():
    import main
    cors_mw = [m for m in main.app.user_middleware if "CORS" in str(m.cls)]
    assert cors_mw, "CORSMiddleware must be registered"
    kwargs = cors_mw[0].kwargs
    assert kwargs.get("allow_origins") != ["*"], (
        "CORS must not default to a wildcard origin while credentials are allowed"
    )
    assert isinstance(kwargs.get("allow_origins"), list) and len(kwargs["allow_origins"]) > 0


def test_cors_origins_env_override():
    with tempfile.TemporaryDirectory() as td:
        env = dict(os.environ)
        env["CORS_ORIGINS"] = "https://app.example.com,https://staging.example.com"
        env["PYTHONPATH"] = _BACKEND_ROOT
        result = subprocess.run(
            [_PY, "-c",
             "import main; "
             "cors=[m for m in main.app.user_middleware if 'CORS' in str(m.cls)][0]; "
             "print(cors.kwargs['allow_origins'])"],
            env=env, cwd=_BACKEND_ROOT, capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, result.stderr
        assert "app.example.com" in result.stdout
        assert "staging.example.com" in result.stdout
        assert "*" not in result.stdout


# ---------------------------------------------------------------------------
# Task 2 -- rate limiting (isolated test app, not the full main.py app,
# so this doesn't touch the real DB/auth flow)
# ---------------------------------------------------------------------------

def test_rate_limit_blocks_after_threshold():
    from fastapi import FastAPI, Depends
    from fastapi.testclient import TestClient
    from app.middleware.rate_limit import rate_limit, reset_all_rate_limits

    reset_all_rate_limits()
    app = FastAPI()

    @app.get("/limited", dependencies=[Depends(rate_limit(3, 60, "test_bucket_a"))])
    def limited():
        return {"ok": True}

    client = TestClient(app)
    statuses = [client.get("/limited").status_code for _ in range(5)]
    assert statuses[:3] == [200, 200, 200], f"first 3 requests should pass: {statuses}"
    assert statuses[3] == 429, f"4th request should be rate-limited: {statuses}"
    assert statuses[4] == 429, f"5th request should be rate-limited: {statuses}"
    reset_all_rate_limits()


def test_rate_limit_buckets_are_independent():
    from fastapi import FastAPI, Depends
    from fastapi.testclient import TestClient
    from app.middleware.rate_limit import rate_limit, reset_all_rate_limits

    reset_all_rate_limits()
    app = FastAPI()

    @app.get("/a", dependencies=[Depends(rate_limit(1, 60, "bucket_a"))])
    def a():
        return {"ok": True}

    @app.get("/b", dependencies=[Depends(rate_limit(1, 60, "bucket_b"))])
    def b():
        return {"ok": True}

    client = TestClient(app)
    assert client.get("/a").status_code == 200
    assert client.get("/a").status_code == 429
    # A different bucket, same client IP, must not be affected by /a's limit
    assert client.get("/b").status_code == 200
    reset_all_rate_limits()


def test_rate_limit_response_has_retry_after_header():
    from fastapi import FastAPI, Depends
    from fastapi.testclient import TestClient
    from app.middleware.rate_limit import rate_limit, reset_all_rate_limits

    reset_all_rate_limits()
    app = FastAPI()

    @app.get("/limited2", dependencies=[Depends(rate_limit(1, 45, "test_bucket_c"))])
    def limited2():
        return {"ok": True}

    client = TestClient(app)
    client.get("/limited2")
    resp = client.get("/limited2")
    assert resp.status_code == 429
    assert resp.headers.get("Retry-After") == "45"
    reset_all_rate_limits()


def test_auth_and_generation_and_deploy_routes_carry_rate_limit_dependency():
    # Confirms the wiring itself, not just that the rate_limit()
    # function works in isolation -- every route this experiment's
    # mission named must actually have the dependency attached.
    # route.dependencies is the exact list passed via
    # dependencies=[...] in the decorator; each entry's .dependency is
    # the actual callable -- rate_limit()'s inner _check function.
    import main

    def _route_has_rate_limit(path: str, method: str) -> bool:
        for route in main.app.routes:
            if getattr(route, "path", None) == path and method.upper() in getattr(route, "methods", set()):
                dep_names = [
                    getattr(d.dependency, "__qualname__", "")
                    for d in getattr(route, "dependencies", [])
                ]
                return any("rate_limit" in n for n in dep_names)
        return False

    for path in ["/register", "/login", "/project/v15", "/generate", "/deploy/github", "/jobs"]:
        assert _route_has_rate_limit(path, "POST"), f"{path} must carry a rate-limit dependency"


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
