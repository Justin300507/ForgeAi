"""
Verifies backend_runner.py's endpoint-smoke-test call passes the actual
`port` the backend is running on, instead of silently defaulting to
run_endpoint_smoke_tests()'s base_url="http://127.0.0.1:8001".

Root cause this fixes (confirmed live, 2026-07-11): BackendRunner.run()
accepts a `port` parameter (used for uvicorn, the health check, and the
CRUD journey), but its call to run_endpoint_smoke_tests(architecture) never
passed base_url, so on any non-default port -- e.g. the V18 parallel batch
runner's dynamic port assignment -- every single endpoint smoke test got a
connection-refused against the wrong port. Confirmed against a real
generated app (todo_list_app) booted on port 8197: endpoint pass rate was
7% (1/14, all connection-refused) before this fix, 100% (14/14) after.
Same bug class as Experiment 039's playwright_workflow.py port fix.

Run directly: python tests/reliability/test_smoke_test_port_wiring.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


def test_smoke_test_call_passes_the_actual_port():
    src_path = os.path.join(os.path.dirname(__file__), "..", "..",
                             "app", "runtime", "backend_runner.py")
    with open(src_path, "r", encoding="utf-8") as f:
        src = f.read()
    call_start = src.index("run_endpoint_smoke_tests(")
    call = src[call_start:call_start + 200]
    assert "base_url=" in call, (
        "run_endpoint_smoke_tests() call must pass base_url derived from "
        "the actual `port` -- calling it with no base_url silently reverts "
        "to the 8001 default regardless of what port the backend booted on."
    )
    assert "port}" in call or "port)" in call, (
        "base_url must be built from the local `port` variable, not a "
        "hardcoded value."
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
