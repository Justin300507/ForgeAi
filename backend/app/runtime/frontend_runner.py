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
    lines = combined.splitlines()
    errors = []

    # Vite's own marker, printed once right before the actual thrown error
    # (type, message, and usually a "file: /abs/path.jsx:12:5" location) --
    # authoritative when present, so grab this block FIRST and return early
    # rather than falling through to the generic per-line matchers below,
    # which have no pattern for a raw internal Rollup crash (a bare
    # TypeError/Error with no ".jsx:N:N:" or "Cannot find module" wording at
    # all -- just N frames of Rollup's own dist/*.js call stack). Missing
    # this meant such a crash produced an EMPTY error list here, silently
    # falling back to the caller's tail[-5:] fallback, which for a deep
    # stack trace is nothing but Rollup's own internal frames -- zero
    # information about which of the project's actual files is broken.
    for i, raw in enumerate(lines):
        if raw.strip() == "error during build:":
            block = [l for l in lines[i + 1:i + 16] if l.strip()]
            if block:
                return ["error during build: " + " | ".join(block)]

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
        # Rollup/vite unresolved import — the most common generated-frontend
        # failure (a page imports a package missing from package.json):
        # [vite]: Rollup failed to resolve import "react-hot-toast" from "src/pages/X.jsx"
        elif "failed to resolve import" in line.lower() or "could not resolve" in line.lower():
            errors.append(line)
        elif line.startswith("RollupError") or line.startswith("[vite]"):
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
        # Force devDependencies to install regardless of the ForgeAI server
        # process's own NODE_ENV. vite, @vitejs/plugin-react, tailwindcss etc.
        # all live in the generated project's devDependencies (correctly --
        # they're build-time only) but `os.environ.copy()` above inherits
        # NODE_ENV=production if the hosting platform sets it for the ForgeAI
        # backend service itself, and npm's classic behavior is to silently
        # skip devDependencies under NODE_ENV=production. Confirmed live
        # (2026-07-16, production Render deploy of habit_tracker): "npm
        # install complete" printed with no error, then every single build
        # attempt failed identically with "sh: 1: vite: not found" -- 5 fix
        # attempts plus a full architecture regeneration all burned trying
        # to patch code that was never the problem, and a 97.6/A+ app never
        # deployed. This verification build always needs the full dep tree,
        # independent of whatever env the server itself runs under.
        env["NODE_ENV"] = "development"

        t0 = time.time()

        # ── npm install (only if node_modules is absent or vite binary missing) ──
        node_modules = project_dir / "node_modules"
        vite_bin = node_modules / ".bin" / "vite"
        vite_bin_cmd = node_modules / ".bin" / "vite.cmd"
        vite_present = vite_bin.exists() or vite_bin_cmd.exists()
        # package.json newer than node_modules ⇒ a patcher added a dependency
        # after the last install (e.g. react-hot-toast). Skipping install here
        # made that fix a no-op: the build kept failing on the same unresolved
        # import while every retry printed "node_modules exists — skipping".
        pkg_changed = False
        try:
            if node_modules.exists() and package_json.exists():
                pkg_changed = package_json.stat().st_mtime > node_modules.stat().st_mtime
        except Exception:
            pass
        if not node_modules.exists() or not vite_present or pkg_changed:
            if pkg_changed:
                print("[Frontend] package.json changed since last install — re-running npm install...")
            elif node_modules.exists():
                print("[Frontend] node_modules exists but vite missing — reinstalling...")
            else:
                print("[Frontend] Installing dependencies (npm install)...")
            from app.utils.proc import run_tree_capped
            # run_tree_capped (Exp114): plain subprocess.run's timeout is a
            # no-op here on Windows — the npm.cmd wrapper dies but the node
            # grandchild holds the pipes and communicate() hangs unbounded
            # (78 min observed live).
            #
            # Two attempts (Exp116): from-scratch installs under a OneDrive-
            # synced tree intermittently exceed any sane timeout (crm's leg
            # timed out 4/4 at 300s while blog's warm-node_modules installs
            # took seconds). Attempt 2 clears the partial node_modules and
            # uses --prefer-offline — by then npm's cache holds most
            # packages, so it's mostly local I/O. --prefer-offline stays OFF
            # for attempt 1: on cache-less hosts (Render) it causes silent
            # ENOENT failures.
            #
            # Attempt-1 timeout (Exp132): this runner is only ever called for
            # local verification builds (engine.py/_run_frontend_build,
            # project_service.py) -- deploy providers build server-side and
            # never touch it -- so the host npm cache is always this same
            # dev machine's, already warm from prior generations. A hung
            # attempt 1 was burning its full budget (observed 420s of a
            # 685s total run) before ever reaching the fast --prefer-offline
            # path. 90s is enough for a real cold-cache network install of a
            # typical React+Vite dep set; a hang past that is the OneDrive
            # I/O issue, not a slow registry, so bail early into attempt 2.
            base_cmd = [npm, "install", "--no-fund", "--no-audit", "--legacy-peer-deps"]
            attempt_timeouts = {1: 90, 2: 300}
            install = None
            for attempt in (1, 2):
                cmd = base_cmd + (["--prefer-offline"] if attempt == 2 else [])
                try:
                    install = run_tree_capped(cmd, cwd=str(project_dir),
                                              timeout=attempt_timeouts[attempt], env=env)
                except subprocess.TimeoutExpired:
                    if attempt == 2:
                        raise
                    print("[Frontend] npm install timed out — clearing partial "
                          "node_modules and retrying with --prefer-offline...")
                    import shutil as _shutil
                    _shutil.rmtree(str(node_modules), ignore_errors=True)
                    continue
                if install.returncode == 0 or attempt == 2:
                    break
                print(f"[Frontend] npm install failed (rc={install.returncode}) — "
                      "clearing node_modules and retrying with --prefer-offline...")
                import shutil as _shutil
                _shutil.rmtree(str(node_modules), ignore_errors=True)
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
        # Cap the V8 heap explicitly: on small containers (Railway) vite/rollup
        # with Node's default heap sizing died with exit 134 (V8 fatal OOM)
        # right after a fresh npm install, and the parser reported "0 errors".
        #
        # The cap used to be 1536MB -- three times the ENTIRE memory budget
        # of the Render free-tier instance ForgeAI's own backend actually
        # runs on (512MB total, shared with the parent Python/FastAPI
        # process). Live incident (2026-07-17): a vite build spawned as a
        # child of that same web service exceeded the instance's real
        # memory limit and Render auto-restarted it mid-generation, killing
        # whatever job was running. 400MB leaves real headroom for the
        # parent process + OS on a 512MB instance; override via
        # FORGE_FRONTEND_BUILD_HEAP_MB for a larger/paid tier.
        if "--max-old-space-size" not in env.get("NODE_OPTIONS", ""):
            heap_mb = os.environ.get("FORGE_FRONTEND_BUILD_HEAP_MB", "400")
            env["NODE_OPTIONS"] = (env.get("NODE_OPTIONS", "") + f" --max-old-space-size={heap_mb}").strip()

        def _is_oom(rc: int, out: str, err: str) -> bool:
            combined = out + err
            return (rc in (134, 137, -6, -9)
                    or "heap out of memory" in combined
                    or "V8::Fatal" in combined
                    or "V8::FatalProcessOutOfMemory" in combined)

        for build_try in (1, 2):
            print("Running vite build...")
            from app.utils.proc import run_tree_capped
            build = run_tree_capped(
                [npm, "run", "build"],
                cwd=project_dir,
                timeout=180,
                env=env,
            )
            if build.returncode != 0 and _is_oom(build.returncode, build.stdout, build.stderr) and build_try == 1:
                # Memory pressure right after npm install is often transient —
                # retry once before reporting a build failure.
                print(f"[Frontend] build hit OOM (exit {build.returncode}) — retrying once...")
                continue
            break
        build_time = round(time.time() - t0, 2)

        errors = _parse_vite_errors(build.stderr, build.stdout)

        if build.returncode != 0:
            # A failed build must NEVER report zero errors — an empty error
            # list made the fix loop patch unrelated files while the real
            # cause (e.g. the OOM crash) stayed invisible.
            if not errors:
                if _is_oom(build.returncode, build.stdout, build.stderr):
                    errors = [f"vite build ran out of memory (exit {build.returncode}) — "
                              "the build machine is memory-constrained; this is an "
                              "environment issue, not a code bug"]
                else:
                    # Prefer the HEAD over the tail: vite/rollup print the
                    # actual error type, message, and (usually) a
                    # "file: /abs/path.jsx:N:N" location near the TOP of the
                    # output, then dump a long internal stack trace
                    # underneath. For a deep internal crash (no line matched
                    # any pattern in _parse_vite_errors) the LAST 5 lines are
                    # guaranteed to be nothing but Rollup's own dist/*.js
                    # call frames -- no reference to any file in the actual
                    # project at all, so the fix-loop LLM had nothing to
                    # ground a patch in and guessed at unrelated files
                    # instead (confirmed live: a build failure that never
                    # once mentioned a real project file got "fixed" by
                    # scaffolding stub pages that were never the problem,
                    # and the identical build error persisted unchanged
                    # across every subsequent attempt).
                    all_lines = [l for l in _strip_ansi((build.stderr or build.stdout or "")).strip().splitlines() if l.strip()]
                    head = all_lines[:10]
                    errors = [f"vite build failed (exit {build.returncode}): "
                              + " | ".join(head)]
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
