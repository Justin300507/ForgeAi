"""
Generation Replay Viewer

CLI tool to inspect and replay a recorded generation run.

Usage:
    python -m app.replay.viewer                  # list all runs
    python -m app.replay.viewer <run-id>         # view run details
    python -m app.replay.viewer <run-id> --stage plan     # show one stage
    python -m app.replay.viewer <run-id> --diag          # show diagnostics
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_REPLAYS_DIR = Path(__file__).parent.parent.parent / "replays"


def list_runs() -> None:
    if not _REPLAYS_DIR.exists():
        print("No replays directory found. Run a generation first.")
        return

    runs = sorted(_REPLAYS_DIR.iterdir(), reverse=True)
    if not runs:
        print("No runs recorded yet.")
        return

    print(f"\n  {'RUN ID':<28} {'SCORE':>6} {'OK':>5} {'TIME':>8}  IDEA")
    print(f"  {'-' * 80}")
    for run_dir in runs[:30]:
        manifest_path = run_dir / "manifest.json"
        if not manifest_path.exists():
            continue
        try:
            m = json.loads(manifest_path.read_text(encoding="utf-8"))
            score = f"{m.get('forge_score', '?'):.0f}" if m.get("forge_score") is not None else "?"
            ok    = "YES" if m.get("success") else "NO "
            dur   = f"{m.get('duration_s', 0):.0f}s" if m.get("duration_s") else "?"
            idea  = m.get("idea", "")[:50]
            print(f"  {m['run_id']:<28} {score:>6} {ok:>5} {dur:>8}  {idea}")
        except Exception:
            print(f"  {run_dir.name:<28} (unreadable manifest)")


def view_run(run_id: str, stage_filter: str | None = None, show_diag: bool = False) -> None:
    run_dir = _REPLAYS_DIR / run_id
    if not run_dir.exists():
        # Try prefix match
        matches = [d for d in _REPLAYS_DIR.iterdir() if d.name.startswith(run_id)]
        if len(matches) == 1:
            run_dir = matches[0]
        elif len(matches) > 1:
            print(f"Ambiguous prefix '{run_id}' matches: {[d.name for d in matches]}")
            return
        else:
            print(f"Run '{run_id}' not found.")
            return

    manifest_path = run_dir / "manifest.json"
    stages_path   = run_dir / "stages.jsonl"
    diag_path     = run_dir / "diagnostics.json"

    # Print manifest
    if manifest_path.exists():
        m = json.loads(manifest_path.read_text(encoding="utf-8"))
        print(f"\n{'=' * 60}")
        print(f"  Run: {m['run_id']}")
        print(f"  Idea: {m['idea']}")
        print(f"  Provider: {m.get('provider', '?')}")
        print(f"  Started: {m.get('started_at', '?')}")
        print(f"  Duration: {m.get('duration_s', '?')}s")
        print(f"  Score: {m.get('forge_score', '?')}")
        print(f"  Success: {m.get('success', '?')}")
        if m.get("error"):
            print(f"  Error: {m['error']}")
        print(f"{'=' * 60}")

    # Print diagnostics
    if show_diag and diag_path.exists():
        diag = json.loads(diag_path.read_text(encoding="utf-8"))
        print(f"\n  STATIC ERRORS ({len(diag.get('static_errors', []))}):")
        for e in diag.get("static_errors", [])[:10]:
            print(f"    {e}")

        print(f"\n  RUNTIME ISSUES ({len(diag.get('runtime_issues', []))}):")
        for i in diag.get("runtime_issues", []):
            print(f"    {i}")

        print(f"\n  JOURNEY STEPS ({len(diag.get('journey_steps', []))}):")
        for s in diag.get("journey_steps", []):
            marker = "✓" if s.get("passed") else "✗"
            print(f"    {marker} {s.get('name', '?')}: {s.get('detail', '')}")

        fixes = diag.get("fix_log", [])
        print(f"\n  FIX LOG ({len(fixes)} fix attempts):")
        for fix in fixes[:10]:
            status = "applied" if fix.get("fix_applied") else "skipped"
            print(f"    [{status}] {fix.get('file', '?')}: {'; '.join(fix.get('errors', [])[:1])}")
        return

    # Print stages
    if not stages_path.exists():
        print("  No stages recorded.")
        return

    print(f"\n  STAGES:")
    print(f"  {'#':>3} {'STAGE':<20} {'ELAPSED':>8} {'TOKENS':>10}  NOTES")
    print(f"  {'-' * 60}")

    for line in stages_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            s = json.loads(line)
        except Exception:
            continue

        if stage_filter and stage_filter not in s.get("stage", ""):
            continue

        seq     = s.get("seq", "?")
        stage   = s.get("stage", "?")[:20]
        elapsed = f"{s.get('elapsed_s', 0):.1f}s"
        tokens  = s.get("tokens", {})
        tok_str = f"{tokens.get('in', 0)}+{tokens.get('out', 0)}" if tokens else "-"
        dur     = f"({s.get('duration_s', 0):.1f}s)" if s.get("duration_s") else ""

        print(f"  {seq:>3} {stage:<20} {elapsed:>8} {tok_str:>10}  {dur}")

        if stage_filter:
            if s.get("prompt_preview"):
                print(f"\n  PROMPT:\n{s['prompt_preview'][:1000]}")
            if s.get("response_preview"):
                print(f"\n  RESPONSE:\n{s['response_preview'][:1000]}")
            elif s.get("response"):
                print(f"\n  RESPONSE:\n{json.dumps(s['response'], indent=2)[:1000]}")


def main():
    args = sys.argv[1:]

    if not args:
        list_runs()
        return

    run_id = args[0]
    stage_filter = None
    show_diag = False

    for i, arg in enumerate(args[1:], 1):
        if arg == "--stage" and i + 1 < len(args):
            stage_filter = args[i + 1]
        elif arg == "--diag":
            show_diag = True

    view_run(run_id, stage_filter=stage_filter, show_diag=show_diag)


if __name__ == "__main__":
    main()
