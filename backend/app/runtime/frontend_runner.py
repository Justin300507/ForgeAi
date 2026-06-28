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

        project_dir = Path(project_path).resolve()
        package_json = project_dir / "package.json"

        # Pre-flight diagnostics — printed unconditionally so failures are debuggable
        print(f"[Frontend] cwd:          {project_dir}")
        print(f"[Frontend] cwd exists:   {project_dir.exists()}")
        print(f"[Frontend] package.json: {'✓ exists' if package_json.exists() else '✗ MISSING'}")
        print(f"[Frontend] npm:          {npm}")

        if not project_dir.exists():
            return FrontendBuildResult(
                success=False,
                exit_code=-1,
                stderr=f"Project directory does not exist: {project_dir}",
            )

        if not package_json.exists():
            # List what IS in the directory to help debug wrong-path issues
            try:
                contents = [p.name for p in project_dir.iterdir()][:20]
            except Exception:
                contents = ["<unable to list>"]
            return FrontendBuildResult(
                success=False,
                exit_code=-1,
                stderr=(
                    f"No package.json in {project_dir}. "
                    f"Directory contains: {contents}. "
                    "Check that the generator wrote frontend files with paths starting "
                    "with src/ (not frontend/src/) or that the template injector ran."
                ),
            )

        print(f"[Frontend] Building in: {project_dir}")

        # Ensure Node's own directory is on PATH so npm post-install scripts
        # (like esbuild's install.js) can call `node` directly.
        node_dir = os.path.dirname(npm)
        env = os.environ.copy()
        if node_dir and node_dir not in env.get("PATH", ""):
            env["PATH"] = node_dir + os.pathsep + env.get("PATH", "")
        # Redirect npm cache to /tmp to avoid ENOENT on restricted filesystems
        # (Railway /data volumes, Docker layers, etc.)
        import tempfile
        env["npm_config_cache"] = str(Path(tempfile.gettempdir()) / ".npm-forge-cache")

        t0 = time.time()

        # ── npm install (only if node_modules is absent or vite binary missing) ──
        node_modules = project_dir / "node_modules"
        vite_bin = node_modules / ".bin" / "vite"
        vite_bin_cmd = node_modules / ".bin" / "vite.cmd"
        vite_present = vite_bin.exists() or vite_bin_cmd.exists()
        if not node_modules.exists() or not vite_present:
            if node_modules.exists():
                print("[Frontend] node_modules exists but vite missing — reinstalling...")
            else:
                print("[Frontend] Installing dependencies (npm install)...")
            install = subprocess.run(
                # No --prefer-offline: Render has no npm cache, that flag causes
                # silent ENOENT failures when packages aren't cached locally.
                # --no-fund / --no-audit keep output clean; --legacy-peer-deps
                # avoids peer conflict aborts on generated dependency sets.
                [npm, "install", "--no-fund", "--no-audit", "--legacy-peer-deps"],
                cwd=str(project_dir),
                capture_output=True,
                text=True,
                timeout=300,
                env=env,
            )
            if install.returncode != 0:
                # Full output — never truncate, debugging blind is the real cost
                diag = (
                    f"Working dir: {project_dir}\n"
                    f"package.json exists: {package_json.exists()}\n"
                    f"node_modules before: {node_modules.exists()}\n\n"
                    f"--- stdout ---\n{install.stdout}\n"
                    f"--- stderr ---\n{install.stderr}"
                )
                print(f"[Frontend] npm install FAILED (rc={install.returncode})")
                print(diag)
                return FrontendBuildResult(
                    success=False,
                    exit_code=install.returncode,
                    stdout=install.stdout,
                    stderr=diag,
                    errors=[f"npm install failed (rc={install.returncode}): {install.stderr[:300]}"],
                    build_time=round(time.time() - t0, 2),
                )
            print("[Frontend] npm install complete")
        else:
            print("[Frontend] node_modules exists — skipping npm install")

        # ── Clear stale dist/ so vite always writes a clean build ─────────
        import shutil as _shutil
        dist_dir = project_dir / "dist"
        if dist_dir.exists():
            try:
                _shutil.rmtree(str(dist_dir))
            except Exception:
                pass

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
