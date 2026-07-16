"""
Exp117: a single transient network error ([WinError 10054]) on the
Cloudflare project-existence API call permanently failed the whole
frontend deploy of a 91.6/A app (exp116-milestone-r6, forge_blog_cms).
_ensure_project_exists now retries transient failures up to 3 times.

Run directly: python tests/reliability/test_exp117_cf_transient_retry.py
"""
import os
import sys
import urllib.error

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.deployments.cloudflare_provider import CloudflareProvider


def _provider():
    p = CloudflareProvider.__new__(CloudflareProvider)
    p.api_token = "t"
    p.account_id = "a"
    return p


def test_transient_errors_then_success_retries_through(monkey_sleep=None):
    p = _provider()
    calls = {"n": 0}

    def fake_once(slug, logs):
        calls["n"] += 1
        if calls["n"] < 3:
            raise urllib.error.URLError(OSError(10054, "connection forcibly closed"))
        return True

    p._ensure_project_exists_once = fake_once
    import time
    real_sleep = time.sleep
    time.sleep = lambda s: None
    try:
        logs = []
        assert p._ensure_project_exists("slug", logs) is True
        assert calls["n"] == 3
        assert any("transient error" in l for l in logs)
    finally:
        time.sleep = real_sleep


def test_persistent_transient_errors_give_up_false():
    p = _provider()

    def fake_once(slug, logs):
        raise ConnectionResetError(10054, "closed")

    p._ensure_project_exists_once = fake_once
    import time
    real_sleep = time.sleep
    time.sleep = lambda s: None
    try:
        logs = []
        assert p._ensure_project_exists("slug", logs) is False
        assert sum("transient error" in l for l in logs) == 3
    finally:
        time.sleep = real_sleep


def test_definitive_answer_not_retried():
    p = _provider()
    calls = {"n": 0}

    def fake_once(slug, logs):
        calls["n"] += 1
        return False  # definitive API "no" — must not retry

    p._ensure_project_exists_once = fake_once
    logs = []
    assert p._ensure_project_exists("slug", logs) is False
    assert calls["n"] == 1


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
