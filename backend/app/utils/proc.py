"""Subprocess helpers with a timeout that actually fires on Windows.

Exp114 (2026-07-16): `subprocess.run([npm, ...], capture_output=True,
timeout=300)` hung for 78 minutes during the exp113-milestone-r4 canary.
On Windows `npm` resolves to a cmd.exe wrapper; on timeout Python kills
that wrapper, but the node.exe GRANDCHILD survives and keeps the
inherited stdout/stderr pipe handles open — so the post-kill
`communicate()` blocks until the orphan exits, turning the documented
timeout into an unbounded hang. This is the mechanism behind ForgeBench
v1's 24% "execution-level hang rate".

`run_tree_capped` kills the ENTIRE process tree (taskkill /T on Windows)
on expiry, then re-raises the standard TimeoutExpired so existing
handlers keep working unchanged.
"""
from __future__ import annotations

import os
import subprocess


def run_tree_capped(cmd, cwd=None, timeout: float = 300, env=None,
                    text: bool = True, input=None) -> subprocess.CompletedProcess:
    p = subprocess.Popen(
        cmd, cwd=cwd, env=env,
        stdin=subprocess.PIPE if input is not None else None,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=text,
    )
    try:
        out, err = p.communicate(input=input, timeout=timeout)
        return subprocess.CompletedProcess(cmd, p.returncode, out, err)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)],
                           capture_output=True)
        else:
            try:
                p.kill()
            except Exception:
                pass
        try:
            out, err = p.communicate(timeout=15)
        except Exception:
            out, err = ("", "") if text else (b"", b"")
        raise subprocess.TimeoutExpired(cmd, timeout, output=out, stderr=err)
