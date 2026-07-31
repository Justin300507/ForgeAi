"""
Experiment 162: CRITICAL regression test -- /credentials, /credentials (POST),
and /credentials/status had NO authentication dependency at all (unlike every
other sensitive endpoint in this codebase, which uses Depends(get_current_user)
or Depends(require_admin)). Confirmed live (2026-08-01): a plain unauthenticated
GET https://<production-host>/credentials returned the real github_token,
vercel_token, and neon_api_key in plaintext. POST /credentials had the same
gap, meaning anyone could also silently overwrite those stored tokens
(e.g. hijacking future GitHub pushes to an attacker's own token).

This does NOT spin up the app or hit the network -- it inspects the route
functions' own signatures to verify the Depends(require_admin) wiring is
actually present, which is the exact thing that was missing despite
require_admin() itself being correctly implemented and tested elsewhere
(test_exp153_admin_lockdown_and_daily_limit.py tests the function in
isolation, not whether any given route actually applies it).

Run directly: python tests/reliability/test_exp162_credentials_endpoint_auth.py
"""
from __future__ import annotations

import inspect
import os
import secrets
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[2]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))
os.environ.setdefault("SECRET_KEY", secrets.token_hex(32))
os.environ.setdefault("SKIP_TABLE_INIT", "1")

import main  # noqa: E402
from app.dependencies.auth import require_admin  # noqa: E402


def _depends_target(func, param_name: str):
    """Returns the callable a FastAPI Depends() default points at, or None
    if the parameter is missing or has a plain (non-Depends) default."""
    sig = inspect.signature(func)
    if param_name not in sig.parameters:
        return None
    default = sig.parameters[param_name].default
    dependency = getattr(default, "dependency", None)
    return dependency


def test_get_credentials_requires_admin():
    assert _depends_target(main.get_credentials, "current_user") is require_admin, (
        "GET /credentials must require admin auth -- it returns raw github_token/"
        "vercel_token/neon_api_key in plaintext"
    )


def test_save_credentials_requires_admin():
    assert _depends_target(main.save_credentials, "current_user") is require_admin, (
        "POST /credentials must require admin auth -- otherwise anyone can silently "
        "overwrite the stored github_token/vercel_token/etc with their own values"
    )


def test_credentials_status_requires_admin():
    assert _depends_target(main.credentials_status, "current_user") is require_admin, (
        "GET /credentials/status must require admin auth"
    )


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
