"""
Generation Replay Recorder

Records every stage of a ForgeAI generation run so it can be replayed,
inspected, and debugged later.

Each run produces a directory under backend/replays/<run-id>/:
  manifest.json      — metadata: idea, version, provider, outcome, timing
  stages.jsonl       — one JSON line per stage (prompt, response, tokens, timing)
  diagnostics.json   — all validation errors, runtime issues, journey steps
  screenshots/       — Playwright screenshots (if captured)

Usage (in project_service.py or pipeline):

    from app.replay.recorder import Recorder

    with Recorder(idea, provider) as rec:
        plan = generate_plan(idea, provider)
        rec.log("plan", prompt=last_prompt, response=plan, tokens={"in": 1200, "out": 300})

        architecture = generate_architecture(plan, provider)
        rec.log("architecture", response=architecture)

        # ... rest of pipeline ...

        rec.finish(forge_score=92, success=True)

Then to inspect:
    python -m app.replay.viewer <run-id>
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Optional


_REPLAYS_DIR = Path(__file__).parent.parent.parent / "replays"

# Global "current recorder" for the active generation run.
# project_service.py sets this at the start of each run.
_active: Optional["Recorder"] = None


def get_active() -> Optional["Recorder"]:
    return _active


class Recorder:
    """
    Context manager that records each pipeline stage to disk.
    Thread-safe enough for single-project use; for batch use, create one per thread.
    """

    def __init__(self, idea: str, provider: str = "auto", run_id: Optional[str] = None):
        self.idea      = idea
        self.provider  = provider
        self.run_id    = run_id or time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
        self.started_at = time.time()
        self._stage_seq = 0

        self._run_dir = _REPLAYS_DIR / self.run_id
        self._run_dir.mkdir(parents=True, exist_ok=True)
        self._stages_path = self._run_dir / "stages.jsonl"
        self._diag_path   = self._run_dir / "diagnostics.json"
        self._manifest_path = self._run_dir / "manifest.json"
        self._screenshots_dir = self._run_dir / "screenshots"

        self._diagnostics: dict[str, Any] = {
            "static_errors": [],
            "runtime_issues": [],
            "journey_steps": [],
            "fix_log": [],
        }

        # Write initial manifest
        self._write_manifest(completed=False, forge_score=None, success=None)

    def __enter__(self) -> "Recorder":
        global _active
        _active = self
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        global _active
        if exc_type:
            self.finish(forge_score=0, success=False, error=str(exc_val))
        _active = None

    def log(
        self,
        stage: str,
        *,
        prompt: Optional[str] = None,
        response: Any = None,
        tokens: Optional[dict] = None,
        duration_s: Optional[float] = None,
        extra: Optional[dict] = None,
    ) -> None:
        """
        Record one pipeline stage.

        Args:
            stage:      Stage name (e.g. "plan", "architecture", "backend", "fix_1")
            prompt:     The full prompt sent to the LLM (truncated to 10KB in storage)
            response:   The response (dict or string)
            tokens:     {"in": int, "out": int, "cost_usd": float}
            duration_s: How long this stage took
            extra:      Any additional metadata
        """
        self._stage_seq += 1
        entry = {
            "seq":       self._stage_seq,
            "stage":     stage,
            "ts":        time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "elapsed_s": round(time.time() - self.started_at, 2),
        }
        if prompt:
            entry["prompt_preview"] = prompt[:2000]  # first 2KB for debugging
            entry["prompt_len"] = len(prompt)
        if response is not None:
            if isinstance(response, str):
                entry["response_preview"] = response[:2000]
                entry["response_len"] = len(response)
            else:
                entry["response"] = response
        if tokens:
            entry["tokens"] = tokens
        if duration_s is not None:
            entry["duration_s"] = round(duration_s, 2)
        if extra:
            entry["extra"] = extra

        with self._stages_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")

    def log_validation(self, errors: list[str]) -> None:
        """Record static validation errors."""
        self._diagnostics["static_errors"] = errors[:50]

    def log_fix(self, filepath: str, errors: list[str], fix_applied: bool) -> None:
        """Record a fix attempt."""
        self._diagnostics["fix_log"].append({
            "file": filepath,
            "errors": errors[:5],
            "fix_applied": fix_applied,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })

    def log_runtime(self, runtime_result: dict) -> None:
        """Record runtime validation result including behavioral issues and journey."""
        issues = runtime_result.get("behavioral_issues") or []
        self._diagnostics["runtime_issues"] = [
            f"{i.get('method','')} {i.get('path','')} → {i.get('issue','')}"
            for i in issues
        ]
        journey = runtime_result.get("journey") or {}
        self._diagnostics["journey_steps"] = journey.get("steps", [])

    def log_screenshot(self, name: str, data: bytes) -> Optional[Path]:
        """Save a screenshot. Returns the saved path or None on failure."""
        try:
            self._screenshots_dir.mkdir(exist_ok=True)
            path = self._screenshots_dir / f"{name}.png"
            path.write_bytes(data)
            return path
        except Exception:
            return None

    def finish(
        self,
        forge_score: Optional[float] = None,
        success: Optional[bool] = None,
        error: Optional[str] = None,
    ) -> None:
        """Finalize the recording."""
        # Save diagnostics
        self._diag_path.write_text(
            json.dumps(self._diagnostics, indent=2, default=str),
            encoding="utf-8",
        )
        # Update manifest
        self._write_manifest(completed=True, forge_score=forge_score, success=success, error=error)
        print(f"  [replay] Run recorded → replays/{self.run_id}/")

    def _write_manifest(
        self,
        completed: bool,
        forge_score: Optional[float],
        success: Optional[bool],
        error: Optional[str] = None,
    ) -> None:
        manifest = {
            "run_id":      self.run_id,
            "idea":        self.idea,
            "provider":    self.provider,
            "started_at":  time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.started_at)),
            "completed":   completed,
            "duration_s":  round(time.time() - self.started_at, 1) if completed else None,
            "forge_score": forge_score,
            "success":     success,
            "error":       error,
            "stages_file": "stages.jsonl",
            "diag_file":   "diagnostics.json",
        }
        self._manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
