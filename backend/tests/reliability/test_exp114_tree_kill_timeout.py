"""
Exp114: subprocess.run's timeout is a no-op when a grandchild holds the
captured pipes (Windows: npm.cmd wrapper dies, node grandchild survives,
communicate() blocks unbounded — 78 minutes observed live in the
exp113-milestone-r4 canary, the mechanism behind ForgeBench v1's 24%
execution-hang rate).

run_tree_capped must (1) actually return control within the timeout
window by killing the WHOLE tree, and (2) leave no orphan grandchild.

Run directly: python tests/reliability/test_exp114_tree_kill_timeout.py
"""
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.utils.proc import run_tree_capped

# Parent spawns a grandchild that inherits stdout (holding our pipe), then
# both sleep far longer than the timeout — the live hang's exact topology.
_GRANDCHILD_HOLDER = (
    "import subprocess, sys, time\n"
    "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
    "print('grandchild_pid=' + str(child.pid), flush=True)\n"
    "time.sleep(120)\n"
)


def _pid_alive(pid: int) -> bool:
    out = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True
    ).stdout if os.name == "nt" else ""
    if os.name == "nt":
        return str(pid) in out
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def test_returns_within_timeout_and_kills_grandchild():
    t0 = time.time()
    grandchild_pid = None
    try:
        run_tree_capped([sys.executable, "-c", _GRANDCHILD_HOLDER], timeout=5)
        raise AssertionError("expected TimeoutExpired")
    except subprocess.TimeoutExpired as e:
        elapsed = time.time() - t0
        # Must come back promptly — the pre-fix behavior blocked until the
        # 120s grandchild exited (or forever, for npm).
        assert elapsed < 40, f"took {elapsed:.1f}s — pipe still held?"
        out = e.output or ""
        for line in str(out).splitlines():
            if line.startswith("grandchild_pid="):
                grandchild_pid = int(line.split("=")[1])
    assert grandchild_pid is not None, "grandchild pid was not captured from drained output"
    time.sleep(1)
    assert not _pid_alive(grandchild_pid), f"grandchild {grandchild_pid} survived the tree kill"


def test_normal_completion_unchanged():
    r = run_tree_capped([sys.executable, "-c", "print('ok')"], timeout=30)
    assert r.returncode == 0
    assert "ok" in r.stdout


def test_bytes_mode_with_input():
    r = run_tree_capped(
        [sys.executable, "-c", "import sys; sys.stdout.write(sys.stdin.read())"],
        timeout=30, text=False, input=b"y\n")
    assert r.returncode == 0
    # Windows text-mode stdout in the child converts \n -> \r\n
    assert r.stdout.replace(b"\r\n", b"\n") == b"y\n"


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
