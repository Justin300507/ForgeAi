"""
Frontend build runner — installs npm deps and runs vite build.
Returns structured result compatible with the V3 pipeline.
"""
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class FrontendBuildResult:
    success: bool
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    errors: list = field(default_factory=list)
    build_time: float = 0.0
    node_missing: bool = False


def _find_npm() -> Optional[str]:
    """Return the path to npm, or None if not installed."""
    npm = shutil.which("npm")
    if npm:
        return npm
    # Common Windows install locations
    candidates = [
        r"C:\Program Files\nodejs\npm.cmd",
        r"C:\Program Files (x86)\nodejs\npm.cmd",
        os.path.join(os.environ.get("APPDATA", ""), "npm", "npm.cmd"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "nodejs", "npm.cmd"),
        r"C:\Program Files\nodejs\npm",
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return None


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _parse_vite_errors(stderr: str, stdout: str) -> list[str]:
    """Extract human-readable build errors from vite/esbuild output."""
    combined = _strip_ansi(stderr + "\n" + stdout)
    errors = []

    for line in combined.splitlines():
        line = line.strip()
        if not line:
            continue
        # esbuild absolute-path error: /abs/path/src/Foo.jsx:3:10: ERROR: ...
        if re.search(r"\.(jsx?|tsx?):\d+:\d+:", line) and "ERROR" in line:
            errors.append(line)
        elif re.search(r"\.(jsx?|tsx?):\d+:\d+:", line) and "error" in line.lower():
            errors.append(line)
        elif line.lower().startswith("error:") or line.startswith("Error:"):
            errors.append(line)
        elif "Cannot find module" in line or "is not defined" in line:
            errors.append(line)
        elif "SyntaxError" in line:
            errors.append(line)

    # De-duplicate while preserving order
    seen: set[str] = set()
    unique = []
    for e in errors:
        if e not in seen:
            seen.add(e)
            unique.append(e)

    return unique[:20]


class FrontendRunner:
    def run(self, project_path: str) -> FrontendBuildResult:
        import time

        npm = _find_npm()
        if not npm:
            return FrontendBuildResult(
                success=False,
                exit_code=-1,
                stderr="npm not found — install Node.js to enable frontend build validation",
                node_missing=True,
            )

        project_dir = Path(project_path)
        package_json = project_dir / "package.json"

        if not package_json.exists():
            return FrontendBuildResult(
                success=False,
                exit_code=-1,
                stderr="No package.json found — frontend templates were not written",
            )

        print(f"Frontend build in: {project_dir}")
        print(f"Using npm: {npm}")

        # Ensure Node's own directory is on PATH so npm post-install scripts
        # (like esbuild's install.js) can call `node` directly.
        node_dir = os.path.dirname(npm)
        env = os.environ.copy()
        if node_dir and node_dir not in env.get("PATH", ""):
            env["PATH"] = node_dir + os.pathsep + env.get("PATH", "")

        t0 = time.time()

        # ── npm install (only if node_modules is absent or stale) ────────
        node_modules = project_dir / "node_modules"
        if not node_modules.exists():
            print("Installing frontend dependencies...")
            install = subprocess.run(
                [npm, "install", "--prefer-offline"],
                cwd=project_dir,
                capture_output=True,
                text=True,
                timeout=180,
                env=env,
            )
            if install.returncode != 0:
                return FrontendBuildResult(
                    success=False,
                    exit_code=install.returncode,
                    stdout=install.stdout,
                    stderr=install.stderr,
                    errors=[f"npm install failed: {install.stderr[:500]}"],
                    build_time=round(time.time() - t0, 2),
                )
            print("npm install complete")
        else:
            print("node_modules exists — skipping npm install")

        # ── npm run build ────────────────────────────────────────────────
        print("Running vite build...")
        build = subprocess.run(
            [npm, "run", "build"],
            cwd=project_dir,
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
        )
        build_time = round(time.time() - t0, 2)

        errors = _parse_vite_errors(build.stderr, build.stdout)

        if build.returncode != 0:
            print(f"Frontend build FAILED in {build_time}s — {len(errors)} errors")
            return FrontendBuildResult(
                success=False,
                exit_code=build.returncode,
                stdout=build.stdout[-3000:],
                stderr=build.stderr[-3000:],
                errors=errors,
                build_time=build_time,
            )

        print(f"Frontend build PASSED in {build_time}s")
        return FrontendBuildResult(
            success=True,
            exit_code=0,
            stdout=build.stdout[-2000:],
            stderr=build.stderr[-1000:],
            errors=[],
            build_time=build_time,
        )
