"""
Exp153 (2026-07-31): the five /admin/* diagnostic routes (read any file
under /data, vacuum the shared DB, wipe node_modules, trigger a redeploy
with arbitrary env vars) were gated by nothing but get_current_user --
fine for a single-operator dev deployment, a real cross-tenant hole the
moment a second real person signs up. Also: GENERATION_RATE_LIMIT only
bounds burst rate (10/60s per IP), nothing capped sustained per-account
usage, and every generation costs real LLM tokens.

Adds require_admin (ADMIN_EMAILS allowlist, fails closed when unset) and
_enforce_daily_generation_limit (DAILY_GENERATION_LIMIT, default 15,
admin-exempt).

Run directly: python tests/reliability/test_exp153_admin_lockdown_and_daily_limit.py
"""
import asyncio
import os
import secrets
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
os.environ.setdefault("SECRET_KEY", secrets.token_hex(32))


def _run(coro):
    return asyncio.run(coro)


# ── require_admin ───────────────────────────────────────────────────────────

def test_require_admin_rejects_non_admin_email():
    from fastapi import HTTPException
    from app.dependencies import auth

    original = auth._ADMIN_EMAILS
    try:
        auth._ADMIN_EMAILS = {"admin@example.com"}
        user = SimpleNamespace(email="stranger@example.com")
        try:
            _run(auth.require_admin(current_user=user))
            assert False, "expected HTTPException"
        except HTTPException as e:
            assert e.status_code == 403
    finally:
        auth._ADMIN_EMAILS = original


def test_require_admin_allows_listed_email_case_insensitive():
    from app.dependencies import auth

    original = auth._ADMIN_EMAILS
    try:
        auth._ADMIN_EMAILS = {"admin@example.com"}
        user = SimpleNamespace(email="ADMIN@EXAMPLE.COM")
        result = _run(auth.require_admin(current_user=user))
        assert result is user
    finally:
        auth._ADMIN_EMAILS = original


def test_require_admin_fails_closed_when_allowlist_empty():
    """An unset ADMIN_EMAILS must deny everyone, never silently grant
    admin to every authenticated user (the bug this replaces)."""
    from fastapi import HTTPException
    from app.dependencies import auth

    original = auth._ADMIN_EMAILS
    try:
        auth._ADMIN_EMAILS = set()
        user = SimpleNamespace(email="anyone@example.com")
        try:
            _run(auth.require_admin(current_user=user))
            assert False, "expected HTTPException"
        except HTTPException as e:
            assert e.status_code == 403
    finally:
        auth._ADMIN_EMAILS = original


# ── _enforce_daily_generation_limit ─────────────────────────────────────────

class _FakeQuery:
    def __init__(self, count_value):
        self._count = count_value

    def filter(self, *_args, **_kwargs):
        return self

    def count(self):
        return self._count


class _FakeDB:
    def __init__(self, count_value):
        self._count = count_value

    def query(self, *_args, **_kwargs):
        return _FakeQuery(self._count)


def test_daily_limit_blocks_at_threshold():
    from fastapi import HTTPException
    import main
    from app.dependencies import auth

    original = auth._ADMIN_EMAILS
    try:
        auth._ADMIN_EMAILS = set()
        db = _FakeDB(main._DAILY_GENERATION_LIMIT)
        user = SimpleNamespace(id=1, email="friend@example.com")
        try:
            main._enforce_daily_generation_limit(db, user)
            assert False, "expected HTTPException"
        except HTTPException as e:
            assert e.status_code == 429
    finally:
        auth._ADMIN_EMAILS = original


def test_daily_limit_allows_under_threshold():
    import main
    from app.dependencies import auth

    original = auth._ADMIN_EMAILS
    try:
        auth._ADMIN_EMAILS = set()
        db = _FakeDB(main._DAILY_GENERATION_LIMIT - 1)
        user = SimpleNamespace(id=1, email="friend@example.com")
        main._enforce_daily_generation_limit(db, user)  # must not raise
    finally:
        auth._ADMIN_EMAILS = original


def test_daily_limit_exempts_admin_regardless_of_count():
    import main
    from app.dependencies import auth

    original = auth._ADMIN_EMAILS
    try:
        auth._ADMIN_EMAILS = {"admin@example.com"}
        db = _FakeDB(main._DAILY_GENERATION_LIMIT * 10)
        user = SimpleNamespace(email="admin@example.com")
        main._enforce_daily_generation_limit(db, user)  # must not raise
    finally:
        auth._ADMIN_EMAILS = original


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
