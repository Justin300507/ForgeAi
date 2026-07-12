"""
Experiment 079: Live Validation of Runtime Endpoint Preservation (Exp078).

Measures whether Exp078's fix to app/repair/orchestrator.py -- normalized
path matching in _relevant_endpoints_for_files() + wiring
_required_endpoints_map_for_files() into generate_architecture_fix() --
actually activates during a real, live generation + repair cycle, and
whether endpoints it's told to preserve actually survive. Same
live-validation methodology Exp074/076 used: reuses run_canary.py's
internals (_check_result, _load_history, _save_history, _acquire_lock,
_release_lock, BENCHMARKS_DIR, CANARY_APPS) WITHOUT modifying that file.

Per this experiment's own constraints: ONE app only (`blog_cms` -- the
exact shape Exp077 traced the confirmed-live failure in), minimal
Cerebras usage, no-deploy, no implementation changes unless a NEW root
cause surfaces.

Instrumentation, all non-invasive (production functions called for real,
only observed):
  1. Wraps orchestrator._required_endpoints_map_for_files in place to log
     every call (files requested -> map returned) -- direct evidence of
     whether the mechanism activates (non-empty map) each time
     _regenerate_module considers a backend file group.
  2. Wraps orchestrator._regenerate_module in place to snapshot every
     affected backend route file immediately before and after the real
     call, whenever the required-endpoints map for that call is
     non-empty -- then AST-extracts (METHOD, path) tuples from each
     snapshot (same approach as endpoint_validator.extract_actual_backend_routes,
     reimplemented here read-only over an in-memory string instead of a
     directory scan) to check every required endpoint is present in both
     the before and after snapshot.
  3. After the run, reads the final project's metadata.json for the
     Architect-planned api_endpoints and calls
     endpoint_validator.extract_actual_backend_routes() (unmodified, real
     function) against the actual final on-disk project -- the same
     ground-truth diff Exp077 used -- to confirm no planned endpoint is
     missing from the delivered app.

Usage:
    python scripts/exp079_canary.py --label exp079-validation-r1 --no-deploy --provider cerebras
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND_ROOT))
sys.path.insert(0, str(_BACKEND_ROOT / "scripts"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import run_canary
from run_canary import (
    BENCHMARKS_DIR, _check_result, _load_history, _save_history,
    _acquire_lock, _release_lock, _regressed,
)

EXP079_APPS = [
    ("blog_cms", "09_blog.txt"),
]

INVOCATIONS_PATH = _BACKEND_ROOT / "benchmark_results" / "exp079_endpoint_preservation_invocations.json"

_activation_log: list[dict] = []   # every _required_endpoints_map_for_files() call
_regen_log: list[dict] = []        # every _regenerate_module() call where the map was non-empty

# _check_result() (run_canary.py, unmodified) only extracts a filtered set
# of fields from generate_project_v15()'s raw result into its own return
# dict -- project_path/project_name aren't among them, but this canary
# needs project_path to locate metadata.json afterward. Wrapping the name
# as bound in run_canary's own module namespace (the actual call site
# inside _check_result) captures the raw result without changing
# run_canary.py itself or _check_result's return contract.
_raw_results_by_app: dict[str, dict] = {}
_current_app_key = {"key": None}


def _normalize_path_segment(path: str) -> str:
    segments = path.strip("/").split("/")
    normalized = ["{}" if seg.startswith("{") and seg.endswith("}") else seg for seg in segments]
    return "/" + "/".join(normalized)


def _extract_endpoints_from_source(content: str) -> set[tuple[str, str]]:
    """Single-file AST endpoint extraction, mirroring
    endpoint_validator.extract_actual_backend_routes()'s per-file logic
    (router-prefix expansion + @router.<method>(path) decorators) but over
    an in-memory string instead of scanning a directory -- lets this
    canary check a specific before/after snapshot pair directly."""
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return set()

    router_prefix = ""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        func = node.value.func
        if not (isinstance(func, ast.Name) and func.id == "APIRouter"):
            continue
        for kw in node.value.keywords:
            if kw.arg == "prefix" and isinstance(kw.value, ast.Constant):
                router_prefix = kw.value.value.rstrip("/")
                break

    found = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                continue
            method = decorator.func.attr.upper()
            if not decorator.args or not isinstance(decorator.args[0], ast.Constant):
                continue
            path = decorator.args[0].value
            if router_prefix:
                rel = path.lstrip("/")
                full_path = router_prefix + ("/" + rel if rel else "")
            else:
                full_path = path
            found.add((method, _normalize_path_segment(full_path)))
    return found


def _make_instrumented_regenerate_module(original_fn, required_endpoints_map_fn):
    def wrapper(group, ctx, cfg):
        affected = group.affected_files or []
        # Calls the REAL (unpatched) _required_endpoints_map_for_files directly
        # -- this is a pure function of ctx.architecture + affected files, so
        # this call and original_fn()'s own internal call to the same
        # (unpatched, unwrapped) function below always agree; only this one
        # call is logged, so activation isn't double-counted.
        required_map = required_endpoints_map_fn(ctx, affected)
        _activation_log.append({
            "files_requested": list(affected),
            "required_endpoints_map": required_map,
            "activated": bool(required_map),
        })
        before_snapshots = {}
        if required_map:
            for rel in required_map:
                target = ctx.project_path / rel
                if target.exists():
                    try:
                        before_snapshots[rel] = target.read_text(encoding="utf-8", errors="replace")
                    except Exception:
                        before_snapshots[rel] = None

        modified, fix_content_map = original_fn(group, ctx, cfg)

        if required_map:
            for rel, required_eps in required_map.items():
                before_content = before_snapshots.get(rel)
                after_content = fix_content_map.get(rel)
                if after_content is None:
                    target = ctx.project_path / rel
                    after_content = target.read_text(encoding="utf-8", errors="replace") if target.exists() else None

                before_found = _extract_endpoints_from_source(before_content) if before_content else set()
                after_found = _extract_endpoints_from_source(after_content) if after_content else set()

                required_tuples = []
                for ep_str in required_eps:
                    method, _, path = ep_str.partition(" ")
                    required_tuples.append((method.upper(), _normalize_path_segment(path)))

                _regen_log.append({
                    "file": rel,
                    "required_endpoints": required_eps,
                    "present_before": [f"{m} {p}" for (m, p) in required_tuples if (m, p) in before_found],
                    "present_after": [f"{m} {p}" for (m, p) in required_tuples if (m, p) in after_found],
                    "missing_after": [f"{m} {p}" for (m, p) in required_tuples if (m, p) not in after_found],
                    "file_was_rewritten": rel in modified,
                })
        return modified, fix_content_map
    return wrapper


def _install_instrumentation():
    import app.repair.orchestrator as orch
    original_map_fn = orch._required_endpoints_map_for_files
    original_regen_fn = orch._regenerate_module

    # _required_endpoints_map_for_files itself is left completely unpatched
    # (production behavior unchanged) -- only _regenerate_module is replaced,
    # and its wrapper calls the original map function directly for
    # observation, exactly once per real call.
    orch._regenerate_module = _make_instrumented_regenerate_module(original_regen_fn, original_map_fn)
    return orch, original_map_fn, original_regen_fn


def _uninstall_instrumentation(orch, original_map_fn, original_regen_fn):
    orch._regenerate_module = original_regen_fn


def _make_result_capturing_generate_project_v15(original_fn):
    def wrapper(*args, **kwargs):
        result = original_fn(*args, **kwargs)
        key = _current_app_key["key"]
        if key is not None:
            _raw_results_by_app[key] = result
        return result
    return wrapper


def _install_result_capture():
    original = run_canary.generate_project_v15
    run_canary.generate_project_v15 = _make_result_capturing_generate_project_v15(original)
    return original


def _uninstall_result_capture(original):
    run_canary.generate_project_v15 = original


def _final_endpoint_inventory(project_path: Path) -> dict:
    """Post-run ground-truth diff: Architect-planned endpoints (metadata.json)
    vs. what's actually in the final delivered backend -- same method
    Exp077 used (extract_actual_backend_routes, unmodified, real function)."""
    from app.services.endpoint_validator import extract_actual_backend_routes, _normalize_path

    meta_path = project_path / "metadata.json"
    if not meta_path.exists():
        return {"error": f"metadata.json not found at {meta_path}"}

    metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    architecture = metadata.get("architecture", {})
    planned = architecture.get("api_endpoints", [])

    planned_set = set()
    planned_detail = []
    for ep in planned:
        method = (ep.get("method") or "").upper()
        path = (ep.get("path") or "").split("?")[0]
        norm = _normalize_path(path)
        planned_set.add((method, norm))
        planned_detail.append({"method": method, "path": path, "file": ep.get("file")})

    actual_set = extract_actual_backend_routes(str(project_path))
    missing = planned_set - actual_set

    return {
        "planned_count": len(planned_set),
        "actual_count": len(actual_set),
        "missing_count": len(missing),
        "missing": [f"{m} {p}" for (m, p) in sorted(missing)],
        "planned_detail": planned_detail,
    }


def main():
    parser = argparse.ArgumentParser(description="Experiment 079 canary: live validation of Exp078's endpoint-preservation fix")
    parser.add_argument("--label", default="exp079-validation")
    parser.add_argument("--no-deploy", action="store_true")
    parser.add_argument("--provider", default="cerebras")
    args = parser.parse_args()

    apps = EXP079_APPS

    orch, original_map_fn, original_regen_fn = _install_instrumentation()
    original_generate_fn = _install_result_capture()
    _acquire_lock(args.label)
    try:
        deploy = not args.no_deploy
        history = _load_history()
        prior_by_app = {}
        if history["runs"]:
            last_run = history["runs"][-1]
            prior_by_app = {r["app"]: r for r in last_run["results"]}

        results = []
        final_inventories = {}
        for idea_key, filename in apps:
            idea = (BENCHMARKS_DIR / filename).read_text(encoding="utf-8").strip()
            _current_app_key["key"] = idea_key
            r = _check_result(idea_key, idea, deploy, provider=args.provider)
            results.append(r)
            raw_result = _raw_results_by_app.get(idea_key, {})
            project_path = raw_result.get("project_path")
            if not project_path:
                project_name = raw_result.get("project_name")
                if project_name:
                    project_path = str(_BACKEND_ROOT.parent / "generated_projects" / project_name)
            if project_path and Path(project_path).exists():
                final_inventories[idea_key] = _final_endpoint_inventory(Path(project_path))
            else:
                final_inventories[idea_key] = {"error": f"could not locate project_path for {idea_key} (crashed={r.get('crashed')})"}

        print(f"\n{'='*70}\n  EXP079 CANARY SUMMARY  ({args.label})\n{'='*70}")
        any_regression = False
        for r in results:
            prev = prior_by_app.get(r["app"])
            regressions = _regressed(prev, r) if prev else []
            status = "REGRESSION" if regressions else ("BASELINE" if not prev else "OK")
            print(f"  [{status:10}] {r['app']:10} score={r['forge_score']:.1f}  "
                  f"build={r['build_ok']} runtime={r['runtime_ok']} crud={r['crud_ok']} "
                  f"browser={r['browser_ok']} deployed={r['deployed']}  ({r['elapsed_s']}s)")
            for reason in regressions:
                print(f"                 -- {reason}")
                any_regression = True

        history["runs"].append({
            "label": args.label,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "deploy": deploy,
            "results": results,
        })
        history["runs"] = history["runs"][-50:]
        _save_history(history)

        INVOCATIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
        INVOCATIONS_PATH.write_text(
            json.dumps({
                "label": args.label,
                "activation_log": _activation_log,
                "regen_log": _regen_log,
                "final_inventories": final_inventories,
            }, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        activations = [a for a in _activation_log if a["activated"]]
        print(f"\n_required_endpoints_map_for_files() calls: {len(_activation_log)} total, "
              f"{len(activations)} activated (non-empty map)")
        for a in activations:
            total_eps = sum(len(v) for v in a["required_endpoints_map"].values())
            print(f"  files={a['files_requested']} -> {total_eps} endpoint(s) across "
                  f"{len(a['required_endpoints_map'])} file(s)")

        print(f"\n_regenerate_module() calls with active preservation: {len(_regen_log)}")
        any_endpoint_lost = False
        for entry in _regen_log:
            lost = entry["missing_after"]
            if lost:
                any_endpoint_lost = True
            print(f"  file={entry['file']} rewritten={entry['file_was_rewritten']} "
                  f"required={len(entry['required_endpoints'])} "
                  f"present_before={len(entry['present_before'])} "
                  f"present_after={len(entry['present_after'])} "
                  f"missing_after={lost}")

        print(f"\nFinal endpoint inventory (architecture vs. delivered app):")
        for app_key, inv in final_inventories.items():
            if "error" in inv:
                print(f"  {app_key}: ERROR -- {inv['error']}")
                continue
            print(f"  {app_key}: planned={inv['planned_count']} actual={inv['actual_count']} "
                  f"missing={inv['missing_count']}")
            if inv["missing"]:
                for m in inv["missing"]:
                    print(f"      MISSING: {m}")

        print(f"\n{'='*70}")
        if any_regression:
            print("  EXP079 CANARY: regression(s) detected vs. prior run")
        if any_endpoint_lost:
            print("  EXP079 CANARY: preservation activated but an endpoint was still lost post-rewrite")
        if not any_regression and not any_endpoint_lost:
            print("  EXP079 CANARY: no regression, no endpoint loss detected")
        print(f"{'='*70}\n")
    finally:
        _release_lock()
        _uninstall_instrumentation(orch, original_map_fn, original_regen_fn)
        _uninstall_result_capture(original_generate_fn)


if __name__ == "__main__":
    main()
