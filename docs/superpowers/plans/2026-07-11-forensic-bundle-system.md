# Forensic Bundle System (V20.1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a generation run hits a failure (starting with `JourneyCRUDFailure`), persist a structured, reusable "forensic bundle" JSON artifact — a schema generic enough for any future failure class (build, deploy, auth, vision) to emit without a schema change — instead of the current 80–200 character truncated string that gets thrown away today.

**Architecture:** A new standalone module (`app/memory/forensic_bundle.py`) owns the bundle schema, a monotonic `FR-NNNNNN` failure-ID sequence, and a `write_bundle()` writer — it has no knowledge of journeys, HTTP, or any specific failure class. The schema reserves a null `artifacts` block (screenshot/console_log/network_log/playwright_trace) from day one, so V20.3 (Browser Evidence) can populate it later without a schema change or migrating bundles written today. The journey runner is instrumented to capture the raw request/response of a failing step (via a thin recorder wrapping `requests`, not by editing each of the 11 step closures). `app/verification/engine.py`'s runtime stage calls `write_bundle()` for `JourneyCRUDFailure` and attaches the resulting `{failure_id, bundle_path}` to the `Diagnostic.metadata`. The V15 pipeline's existing generation-log write (`app/core/pipeline.py`, the one telemetry sink already confirmed live) picks that reference up automatically and persists it to `generation_log.jsonl` — the same store `reliability_metrics.py`/`failure_report.py` already read for the V20 dashboard.

**Tech Stack:** Python 3.10+ stdlib only (`json`, `re`, `subprocess`, `datetime`, `pathlib`, `dataclasses`) — no new dependencies, consistent with the existing `failure_memory.py` / `failure_db.py` modules.

## Global Constraints

- **No screenshots, no replay UI, no heatmap in this cycle.** Those are V20.2/V20.3/V20.4 — explicitly out of scope here (per user direction).
- **Generic schema, not journey-specific.** `forensic_bundle.py` must not import or reference anything journey-related — `stage`/`failure_class`/`step`/`request`/`response` are plain strings/dicts any caller supplies.
- **$0 cost.** Every verification step in this plan runs local Python, no LLM calls, no canary run. A canary run to observe a real bundle from a live generation is a natural follow-up the user triggers separately once this lands.
- **Redact auth tokens.** Bundles are JSON files under `backend/failure_memory/` (git-trackable, same as `patterns.json`). Never write a raw `Authorization` header value into a bundle — store only whether auth was present.
- **Bundle filename:** `{failure_id}_{yyyyMMdd_HHMMSS}_{project}_{failure_class_lower}.json` (combines the user's timestamp-based example and the FR-ID cross-reference — the ID is the stable reference used elsewhere, the timestamp/project/class suffix keeps the directory human-browsable).

---

## File Structure

| File | Responsibility |
|---|---|
| `backend/app/memory/forensic_bundle.py` (new) | Bundle schema, `next_failure_id()`, `write_bundle()`, git SHA lookup. Zero knowledge of failure classes. |
| `backend/failure_memory/bundles/` (new dir, created on first write) | Bundle JSON files. |
| `backend/failure_memory/failure_id_seq.json` (new, created on first write) | `{"next": N}` monotonic counter. |
| `backend/app/runtime/user_journey_runner.py` (modify) | `JourneyStep` gains `request`/`response` fields; a recorder captures the last HTTP exchange per step without touching the 11 step closures. |
| `backend/app/runtime/backend_runner.py` (modify) | Surfaces the new `request`/`response` fields in `journey_data["steps"]` (mirrors the existing `seed_summary` precedent). |
| `backend/app/verification/engine.py` (modify) | New `_write_journey_bundle()` helper; wires it into `_run_runtime_validation()`'s `JourneyCRUDFailure` branch; attaches `bundle_ref` to `Diagnostic.metadata`. |
| `backend/app/knowledge/failure_db.py` (modify) | `GenerationRecord` gains a `bundle_refs: list` field. |
| `backend/app/core/pipeline.py` (modify) | Extracts `bundle_ref` from `ctx.all_diagnostics()` metadata into the `GenerationRecord` it already writes. |

---

### Task 1: Forensic Bundle module (schema, ID sequence, writer)

**Files:**
- Create: `backend/app/memory/forensic_bundle.py`
- Test: `backend/tests/reliability/test_forensic_bundle.py`

**Interfaces:**
- Produces: `write_bundle(*, project: str, stage: str, failure_class: str, step: str|None=None, provider: str|None=None, model: str|None=None, seed: str|None=None, request: dict|None=None, response: dict|None=None, stderr: str|None=None, pipeline_version: str|None=None, generation: dict|None=None) -> dict` returning `{"failure_id": str, "bundle_path": str}`.
- Produces: `next_failure_id() -> str` (format `"FR-000001"`, zero-padded 6 digits, monotonic).
- Produces: `BUNDLE_DIR: Path` — `backend/failure_memory/bundles/`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/reliability/test_forensic_bundle.py`:

```python
"""
Verifies the Forensic Bundle writer: monotonic failure IDs, a generic
schema any failure class can populate, auth redaction, and that the
returned {failure_id, bundle_path} actually resolves to a written file.
Run directly: python tests/reliability/test_forensic_bundle.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.memory import forensic_bundle


def test_next_failure_id_format_and_monotonic():
    a = forensic_bundle.next_failure_id()
    b = forensic_bundle.next_failure_id()
    assert a.startswith("FR-") and len(a) == 9
    assert b.startswith("FR-") and len(b) == 9
    assert int(b.split("-")[1]) == int(a.split("-")[1]) + 1


def test_write_bundle_returns_failure_id_and_path():
    result = forensic_bundle.write_bundle(
        project="unit_test_project",
        stage="runtime",
        failure_class="JourneyCRUDFailure",
        step="Create entity",
        provider="gemini",
        request={"method": "POST", "url": "http://x/items", "json": {"a": 1}},
        response={"status_code": 422, "body": {"detail": "bad"}},
    )
    assert result["failure_id"].startswith("FR-")
    assert result["bundle_path"].startswith("failure_memory/bundles/")
    assert result["bundle_path"].endswith(".json")


def test_write_bundle_file_has_generic_schema():
    result = forensic_bundle.write_bundle(
        project="unit_test_project",
        stage="deployment",
        failure_class="DeployFailure",
        step=None,
        request=None,
        response=None,
        stderr="some traceback",
    )
    repo_root = os.path.join(os.path.dirname(__file__), "..", "..", "..")
    full_path = os.path.join(repo_root, result["bundle_path"])
    with open(full_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert data["bundle_version"] == 1
    assert data["failure_id"] == result["failure_id"]
    assert data["failure"] == {"stage": "deployment", "class": "DeployFailure", "step": None}
    assert data["project"] == "unit_test_project"
    assert data["request"] is None
    assert data["response"] is None
    assert data["stderr"] == "some traceback"
    # Generic metadata fields must exist even when the caller doesn't supply them
    assert "generation" in data
    assert set(data["generation"].keys()) >= {"category", "style", "layout", "design_fingerprint_id"}
    assert "commit_sha" in data
    assert "forgeai_version" in data
    assert "pipeline_version" in data
    # Reserved for V20.3 (Browser Evidence) — must exist, all null, from day one
    # so no future bundle needs a schema migration.
    assert data["artifacts"] == {
        "screenshot": None, "console_log": None,
        "network_log": None, "playwright_trace": None,
    }


def test_write_bundle_never_stores_raw_auth_header():
    result = forensic_bundle.write_bundle(
        project="unit_test_project",
        stage="runtime",
        failure_class="JourneyCRUDFailure",
        request={"method": "POST", "url": "http://x/items",
                 "json": {"a": 1}, "has_auth": True},
        response={"status_code": 401, "body": "no"},
    )
    repo_root = os.path.join(os.path.dirname(__file__), "..", "..", "..")
    full_path = os.path.join(repo_root, result["bundle_path"])
    raw_text = open(full_path, "r", encoding="utf-8").read()
    assert "Bearer " not in raw_text
    assert "Authorization" not in raw_text or '"has_auth"' in raw_text


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python tests/reliability/test_forensic_bundle.py`
Expected: `ERROR: ...` / `ModuleNotFoundError: No module named 'app.memory.forensic_bundle'` (the module doesn't exist yet).

- [ ] **Step 3: Write the implementation**

Create `backend/app/memory/forensic_bundle.py`:

```python
"""
Forensic Bundle System (V20.1)

A generic, versioned evidence artifact any failure class can emit —
JourneyCRUDFailure today, build/deploy/auth/vision failures later,
without a schema change. Each bundle is one JSON file under
backend/failure_memory/bundles/, cross-referenced everywhere else
(generation_log.jsonl, dashboards, experiments.md) by its failure_id.

This module deliberately knows nothing about journeys, HTTP, or any
specific failure class — callers supply stage/failure_class/step and
whatever request/response/stderr evidence they have.

Storage:
  backend/failure_memory/bundles/*.json       — one file per failure
  backend/failure_memory/failure_id_seq.json  — monotonic ID counter
"""
import json
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path

_MEM_DIR    = Path(__file__).parent.parent.parent / "failure_memory"
BUNDLE_DIR  = _MEM_DIR / "bundles"
_SEQ_PATH   = _MEM_DIR / "failure_id_seq.json"
_REPO_ROOT  = Path(__file__).parent.parent.parent.parent

# Keep in sync with backend/main.py's FastAPI(version=...) — there is no
# shared constant today (main.py's own comment says the same about its two
# copies), so this is a third copy following the same established pattern.
FORGEAI_VERSION = "19.0"

_sha_cache: list = []  # [] = not yet computed, [sha_or_None] = cached


def next_failure_id() -> str:
    """Monotonic FR-NNNNNN id, persisted across process restarts."""
    seq = {"next": 1}
    if _SEQ_PATH.exists():
        try:
            seq = json.loads(_SEQ_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    n = seq.get("next", 1)
    _MEM_DIR.mkdir(parents=True, exist_ok=True)
    _SEQ_PATH.write_text(json.dumps({"next": n + 1}), encoding="utf-8")
    return f"FR-{n:06d}"


def _git_commit_sha() -> str | None:
    if not _sha_cache:
        sha = None
        try:
            out = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=str(_REPO_ROOT), capture_output=True, text=True, timeout=3,
            )
            if out.returncode == 0:
                sha = out.stdout.strip() or None
        except Exception:
            sha = None
        _sha_cache.append(sha)
    return _sha_cache[0]


def _safe_filename_part(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", s)[:60]


def write_bundle(
    *,
    project: str,
    stage: str,
    failure_class: str,
    step: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    seed: str | None = None,
    request: dict | None = None,
    response: dict | None = None,
    stderr: str | None = None,
    pipeline_version: str | None = None,
    generation: dict | None = None,
) -> dict:
    """
    Write one forensic bundle and return {"failure_id": ..., "bundle_path": ...}
    (bundle_path is relative to the repo root, e.g. "failure_memory/bundles/...").
    """
    failure_id = next_failure_id()
    ts = datetime.utcnow()

    bundle = {
        "bundle_version": 1,
        "failure_id": failure_id,
        "timestamp": ts.isoformat() + "Z",
        "forgeai_version": FORGEAI_VERSION,
        "pipeline_version": pipeline_version or os.environ.get("FORGE_PIPELINE_VERSION", "v15"),
        "commit_sha": _git_commit_sha(),
        "project": project,
        "provider": provider,
        "model": model,
        "seed": seed,
        "failure": {"stage": stage, "class": failure_class, "step": step},
        "request": request,
        "response": response,
        "stderr": stderr[-4000:] if stderr else None,
        "generation": generation or {
            "category": None,
            "style": None,
            "layout": None,
            "design_fingerprint_id": None,
        },
        # Reserved for V20.3 (Browser Evidence) so those bundles need no
        # schema change and no migration of bundles written today.
        "artifacts": {
            "screenshot": None,
            "console_log": None,
            "network_log": None,
            "playwright_trace": None,
        },
    }

    fname = (f"{failure_id}_{ts.strftime('%Y%m%d_%H%M%S')}_"
             f"{_safe_filename_part(project)}_{failure_class.lower()}.json")
    BUNDLE_DIR.mkdir(parents=True, exist_ok=True)
    (BUNDLE_DIR / fname).write_text(
        json.dumps(bundle, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    return {"failure_id": failure_id, "bundle_path": f"failure_memory/bundles/{fname}"}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python tests/reliability/test_forensic_bundle.py`
Expected: `4/4 passed`

- [ ] **Step 5: Commit**

```bash
git add backend/app/memory/forensic_bundle.py backend/tests/reliability/test_forensic_bundle.py
git commit -m "Add generic Forensic Bundle writer (V20.1 foundation)"
```

---

### Task 2: Capture request/response evidence in the journey runner

**Files:**
- Modify: `backend/app/runtime/user_journey_runner.py:15-20` (JourneyStep), `:287-293` (`_step` → rename to `_run_step`), and insert a recorder + local `_step` shadow around `:307-316`
- Modify: `backend/app/runtime/backend_runner.py:272-275`
- Test: `backend/tests/reliability/test_journey_evidence_capture.py`

**Interfaces:**
- Consumes: nothing new — `run_user_journey(project_path, architecture=None, backend_port=8001)` keeps its existing signature.
- Produces: `JourneyStep.request: dict | None`, `JourneyStep.response: dict | None`, populated only on failed steps that made an HTTP call. `backend_runner.py`'s `journey_data["steps"][i]` dicts gain the same two keys.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/reliability/test_journey_evidence_capture.py`:

```python
"""
Verifies the journey runner attaches request/response evidence to failed
steps (for the Forensic Bundle system) without changing any step
closure's return signature, and that backend_runner.py surfaces it.
Run directly: python tests/reliability/test_journey_evidence_capture.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.runtime.user_journey_runner import JourneyStep, _ExchangeRecorder


class _FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body
        self.text = str(body)

    def json(self):
        if isinstance(self._body, dict):
            return self._body
        raise ValueError("not json")


class _FakeRequestsModule:
    def __init__(self, response):
        self._response = response
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return self._response

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self._response


def test_journey_step_defaults_request_response_to_none():
    step = JourneyStep(name="x", passed=True, duration_ms=1.0)
    assert step.request is None
    assert step.response is None


def test_recorder_captures_last_exchange_on_post():
    fake = _FakeRequestsModule(_FakeResponse(422, {"detail": "bad field"}))
    recorder = _ExchangeRecorder(fake)
    resp = recorder.post("http://x/items", json={"a": 1}, headers={"Authorization": "Bearer secret"})
    assert resp.status_code == 422
    assert recorder.last_exchange["request"]["method"] == "POST"
    assert recorder.last_exchange["request"]["json"] == {"a": 1}
    assert recorder.last_exchange["request"]["has_auth"] is True
    assert "Authorization" not in recorder.last_exchange["request"]
    assert recorder.last_exchange["response"]["status_code"] == 422
    assert recorder.last_exchange["response"]["body"] == {"detail": "bad field"}


def test_backend_runner_surfaces_request_response_in_journey_data():
    src_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "app", "runtime", "backend_runner.py"
    )
    with open(src_path, "r", encoding="utf-8") as f:
        src = f.read()
    block_start = src.index('journey_data = {')
    block = src[block_start:block_start + 900]
    assert '"request": s.request' in block
    assert '"response": s.response' in block


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python tests/reliability/test_journey_evidence_capture.py`
Expected: `ERROR: ... ImportError: cannot import name '_ExchangeRecorder'`

- [ ] **Step 3: Implement — `JourneyStep` fields + `_ExchangeRecorder`**

In `backend/app/runtime/user_journey_runner.py`, change the `JourneyStep` dataclass (currently lines 15-20):

```python
@dataclass
class JourneyStep:
    name: str
    passed: bool
    duration_ms: float
    detail: str = ""
    request: dict | None = None
    response: dict | None = None
```

Immediately after it (before `JourneyResult`), add:

```python
class _ExchangeRecorder:
    """
    Thin wrapper around the `requests` module used only inside
    run_user_journey(). Records the (method, url, json body) / (status,
    body) of the LAST HTTP call made, so a failed step can attach that
    exchange as forensic evidence without changing the return signature
    of any of the step closures (do_register, do_create, ...). Never
    records the raw Authorization header — only whether one was sent —
    since bundles are written to a git-trackable directory.
    """

    def __init__(self, requests_mod):
        self._requests = requests_mod
        self.last_exchange: dict | None = None

    def _wrap(self, verb: str):
        real_fn = getattr(self._requests, verb)

        def call(url, **kwargs):
            resp = real_fn(url, **kwargs)
            try:
                body = resp.json()
            except Exception:
                body = (resp.text or "")[:1000]
            self.last_exchange = {
                "request": {
                    "method": verb.upper(),
                    "url": url,
                    "json": kwargs.get("json"),
                    "has_auth": bool((kwargs.get("headers") or {}).get("Authorization")),
                },
                "response": {
                    "status_code": resp.status_code,
                    "body": body,
                },
            }
            return resp

        return call

    def __getattr__(self, name):
        if name in ("post", "get", "put", "delete", "patch"):
            return self._wrap(name)
        return getattr(self._requests, name)
```

- [ ] **Step 4: Run test to verify Steps 1-3 pass**

Run: `cd backend && python tests/reliability/test_journey_evidence_capture.py`
Expected: `test_journey_step_defaults_request_response_to_none` and `test_recorder_captures_last_exchange_on_post` PASS; `test_backend_runner_surfaces_request_response_in_journey_data` still FAILs (Task 2 Step 6 not done yet).

- [ ] **Step 5: Wire the recorder into `run_user_journey()`**

Rename the module-level `_step` function (currently lines 287-293) to `_run_step`:

```python
def _run_step(name: str, fn) -> JourneyStep:
    t0 = time.time()
    try:
        ok, detail = fn()
        return JourneyStep(name=name, passed=ok, duration_ms=(time.time() - t0) * 1000, detail=detail)
    except Exception as e:
        return JourneyStep(name=name, passed=False, duration_ms=(time.time() - t0) * 1000, detail=str(e))
```

Inside `run_user_journey()`, right after the existing `import requests` try/except block (the block ending at the line before `base = f"http://127.0.0.1:{backend_port}"`), insert:

```python
    recorder = _ExchangeRecorder(requests)
    requests = recorder

    def _step(name: str, fn) -> JourneyStep:
        recorder.last_exchange = None
        step = _run_step(name, fn)
        if not step.passed and recorder.last_exchange:
            step.request = recorder.last_exchange["request"]
            step.response = recorder.last_exchange["response"]
        return step
```

This local `_step` shadows the (now-renamed) module-level function only within `run_user_journey`'s scope — all 11 existing `steps.append(_step("...", do_x))` call sites and the one `detect_step = _step(...)` call need no changes at all.

- [ ] **Step 6: Surface the new fields in `backend_runner.py`**

In `backend/app/runtime/backend_runner.py`, change the `"steps"` list comprehension (currently lines 272-275):

```python
                    "steps": [
                        {"name": s.name, "passed": s.passed, "detail": s.detail,
                         "request": s.request, "response": s.response}
                        for s in journey_result.steps
                    ],
```

- [ ] **Step 7: Run test to verify it passes**

Run: `cd backend && python tests/reliability/test_journey_evidence_capture.py`
Expected: `3/3 passed`

- [ ] **Step 8: Sanity check no other caller depended on the old `_step` name**

Run: `cd backend && grep -rn "_step(" app/ | grep -v user_journey_runner.py`
Expected: no output (confirms the rename in Step 5 didn't break another file).

- [ ] **Step 9: Commit**

```bash
git add backend/app/runtime/user_journey_runner.py backend/app/runtime/backend_runner.py backend/tests/reliability/test_journey_evidence_capture.py
git commit -m "Capture request/response evidence for failed journey steps"
```

---

### Task 3: Wire journey failures into the Forensic Bundle writer

**Files:**
- Modify: `backend/app/verification/engine.py` (add `_write_journey_bundle`; wire into `_run_runtime_validation`)
- Test: `backend/tests/reliability/test_engine_bundle_wiring.py`

**Interfaces:**
- Consumes: `forensic_bundle.write_bundle(...)` (Task 1), `journey["steps"][i]["request"/"response"]` (Task 2).
- Produces: `_write_journey_bundle(ctx, journey: dict) -> dict | None` — returns `{"failure_id": ..., "bundle_path": ...}` or `None`. `Diagnostic.metadata["bundle_ref"]` set on the `JourneyCRUDFailure` diagnostic when a bundle was written.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/reliability/test_engine_bundle_wiring.py`:

```python
"""
Verifies engine.py writes a Forensic Bundle for a failed CRUD journey step
and attaches {failure_id, bundle_path} to the diagnostic's metadata, using
a synthetic journey dict (no real HTTP server needed).
Run directly: python tests/reliability/test_engine_bundle_wiring.py
"""
import json
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.verification.engine import _write_journey_bundle


def _ctx(project_name="demo_project", provider="gemini"):
    return SimpleNamespace(project_name=project_name, current_provider=provider)


def test_returns_none_when_no_step_has_evidence():
    journey = {"steps": [{"name": "Register", "passed": False, "detail": "404"}]}
    assert _write_journey_bundle(_ctx(), journey) is None


def test_returns_none_when_journey_succeeded():
    journey = {"steps": [{"name": "Register", "passed": True, "detail": "201",
                           "request": {"method": "POST"}, "response": {"status_code": 201}}]}
    assert _write_journey_bundle(_ctx(), journey) is None


def test_writes_bundle_for_failed_step_with_evidence():
    journey = {
        "steps": [
            {"name": "Register", "passed": True, "detail": "201"},
            {"name": "Create entity", "passed": False, "detail": "422",
             "request": {"method": "POST", "url": "http://x/items", "json": {"a": 1}},
             "response": {"status_code": 422, "body": {"detail": "bad"}}},
        ]
    }
    result = _write_journey_bundle(_ctx(project_name="demo_project", provider="groq"), journey)
    assert result is not None
    assert result["failure_id"].startswith("FR-")

    repo_root = os.path.join(os.path.dirname(__file__), "..", "..", "..")
    full_path = os.path.join(repo_root, result["bundle_path"])
    with open(full_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert data["project"] == "demo_project"
    assert data["provider"] == "groq"
    assert data["failure"]["class"] == "JourneyCRUDFailure"
    assert data["failure"]["step"] == "Create entity"
    assert data["request"]["json"] == {"a": 1}
    assert data["response"]["status_code"] == 422


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python tests/reliability/test_engine_bundle_wiring.py`
Expected: `ERROR: ... ImportError: cannot import name '_write_journey_bundle'`

- [ ] **Step 3: Implement `_write_journey_bundle` in `engine.py`**

In `backend/app/verification/engine.py`, add this function right after `_find_route_file_for_entity` (which ends at line 462, just before `def _run_runtime_validation`):

```python
def _write_journey_bundle(ctx, journey: dict) -> dict | None:
    """
    Persist a Forensic Bundle for the first failed journey step that
    captured request/response evidence (Task 2's _ExchangeRecorder).
    Returns None if the journey passed, or if the failing step never made
    an HTTP call before raising (nothing to bundle).
    """
    steps = journey.get("steps", [])
    failed = [s for s in steps if not s.get("passed") and (s.get("request") or s.get("response"))]
    if not failed:
        return None
    target = failed[0]
    from app.memory.forensic_bundle import write_bundle
    return write_bundle(
        project=getattr(ctx, "project_name", "unknown"),
        stage="runtime",
        failure_class="JourneyCRUDFailure",
        step=target.get("name"),
        provider=getattr(ctx, "current_provider", None),
        request=target.get("request"),
        response=target.get("response"),
    )
```

- [ ] **Step 4: Run test to verify Step 3 passes**

Run: `cd backend && python tests/reliability/test_engine_bundle_wiring.py`
Expected: `3/3 passed`

- [ ] **Step 5: Wire it into `_run_runtime_validation`'s JourneyCRUDFailure branch**

In `backend/app/verification/engine.py`, inside `_run_runtime_validation`, find:

```python
        elif err_type == "JourneyCRUDFailure" or journey_failed:
            message = (f"Backend healthy but CRUD journey failed — {steps_txt}"
                       if steps_txt else "Backend healthy but CRUD journey failed")
            err_type = "JourneyCRUDFailure"
```

Replace with:

```python
        bundle_ref = None
        if err_type == "JourneyCRUDFailure" or journey_failed:
            message = (f"Backend healthy but CRUD journey failed — {steps_txt}"
                       if steps_txt else "Backend healthy but CRUD journey failed")
            err_type = "JourneyCRUDFailure"
            try:
                bundle_ref = _write_journey_bundle(ctx, journey)
            except Exception:
                bundle_ref = None  # never let bundle writing break verification
        elif True:
            pass
```

Then find the `elif not journey_failed and message == "Backend failed to start":` branch that currently sits right after — since Python `elif` chains only allow one condition per branch, restructure the three-way chain (`if has_specific / elif JourneyCRUDFailure / elif Backend failed to start`) like this instead — replace the **entire** `if has_specific: ... elif err_type == "JourneyCRUDFailure" or journey_failed: ... elif not journey_failed and message == "Backend failed to start": ...` chain with:

```python
        bundle_ref = None
        if has_specific:
            tail = ""
            for line in reversed((stderr or "").splitlines()):
                s = line.strip()
                if s and ("Error" in s or "Exception" in s or "constraint failed" in s):
                    tail = s
                    break
            message = tail or parsed.get("hint") or parsed.get("type")
            if steps_txt:
                message = f"{message}  [CRUD journey failed — {steps_txt}]"
        elif err_type == "JourneyCRUDFailure" or journey_failed:
            message = (f"Backend healthy but CRUD journey failed — {steps_txt}"
                       if steps_txt else "Backend healthy but CRUD journey failed")
            err_type = "JourneyCRUDFailure"
            try:
                bundle_ref = _write_journey_bundle(ctx, journey)
            except Exception:
                bundle_ref = None  # never let bundle writing break verification
        elif not journey_failed and message == "Backend failed to start":
            pass_rate = result.get("endpoint_pass_rate")
            issues = result.get("behavioral_issues") or []
            if pass_rate is not None and issues:
                issues_txt = "; ".join(
                    f"{i.get('method')} {i.get('path')} -> {i.get('issue')}" for i in issues[:4]
                )
                message = f"Endpoint pass rate {pass_rate:.0%} — {issues_txt}"
                err_type = "EndpointSmokeFailure"
```

(This is the same existing chain, unchanged except for the two new lines that call `_write_journey_bundle` inside the `JourneyCRUDFailure` branch — shown in full so the `bundle_ref` variable's scope is unambiguous.)

Finally, find the `Diagnostic(...)` construction a few lines below:

```python
        diagnostics.append(Diagnostic(
            error_id=Diagnostic.make_id("runtime", cat_map.get(err_type, ErrorCategory.RUNTIME),
                                        f"[{err_type}] {message}", error_file),
            category=cat_map.get(err_type, ErrorCategory.RUNTIME),
            severity=ErrorSeverity.CRITICAL,
            source="runtime",
            message=f"[{err_type}] {message}",
            file_path=error_file,
            stack_trace=stderr[-1500:] if stderr else None,
            fix_hint=parsed.get("hint"),
            metadata={"parsed_error": parsed},
        ))
```

Change the last line to:

```python
            metadata={"parsed_error": parsed, "bundle_ref": bundle_ref},
```

- [ ] **Step 6: Run the full test suite for this task plus Task 1/2's suites**

Run:
```bash
cd backend
python tests/reliability/test_forensic_bundle.py
python tests/reliability/test_journey_evidence_capture.py
python tests/reliability/test_engine_bundle_wiring.py
```
Expected: all three print `N/N passed` with exit code 0.

- [ ] **Step 7: Commit**

```bash
git add backend/app/verification/engine.py backend/tests/reliability/test_engine_bundle_wiring.py
git commit -m "Write a Forensic Bundle when the runtime stage detects JourneyCRUDFailure"
```

---

### Task 4: Surface bundle references in the generation log

**Files:**
- Modify: `backend/app/knowledge/failure_db.py:164-172` (`GenerationRecord`)
- Modify: `backend/app/core/pipeline.py:338-354` (the `generation_log.record(...)` call)
- Test: `backend/tests/reliability/test_generation_record_bundle_refs.py`

**Interfaces:**
- Consumes: `Diagnostic.metadata["bundle_ref"]` (Task 3).
- Produces: `GenerationRecord.bundle_refs: list[dict]` — defaults to `[]`, each entry `{"failure_id": ..., "bundle_path": ...}`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/reliability/test_generation_record_bundle_refs.py`:

```python
"""
Verifies GenerationRecord carries bundle_refs (defaulting to empty,
round-tripping through the same asdict()/json path generation_log.jsonl
already uses), and that pipeline.py's generation_log write extracts
bundle_ref from diagnostic metadata into it.
Run directly: python tests/reliability/test_generation_record_bundle_refs.py
"""
import json
import os
import sys
from dataclasses import asdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.knowledge.failure_db import GenerationRecord


def test_generation_record_defaults_bundle_refs_to_empty_list():
    rec = GenerationRecord(
        idea="x", attempt_number=1, final_score=90.0, succeeded=True,
        fix_count=0, dominant_errors=[], architecture_hash="abc123",
    )
    assert rec.bundle_refs == []


def test_generation_record_round_trips_bundle_refs_through_json():
    rec = GenerationRecord(
        idea="x", attempt_number=1, final_score=40.0, succeeded=False,
        fix_count=2, dominant_errors=["[JourneyCRUDFailure] ..."],
        architecture_hash="abc123",
        bundle_refs=[{"failure_id": "FR-000001", "bundle_path": "failure_memory/bundles/x.json"}],
    )
    line = json.dumps(asdict(rec))
    restored = GenerationRecord(**json.loads(line))
    assert restored.bundle_refs == [{"failure_id": "FR-000001",
                                      "bundle_path": "failure_memory/bundles/x.json"}]


def test_pipeline_extracts_bundle_ref_from_diagnostic_metadata():
    src_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "app", "core", "pipeline.py"
    )
    with open(src_path, "r", encoding="utf-8") as f:
        src = f.read()
    block_start = src.index("generation_log.record(GenerationRecord(")
    block = src[block_start:block_start + 900]
    assert "bundle_refs=" in block
    assert 'metadata.get("bundle_ref")' in block


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python tests/reliability/test_generation_record_bundle_refs.py`
Expected: `FAIL: test_generation_record_defaults_bundle_refs_to_empty_list: ... unexpected keyword argument` or `AttributeError` (field doesn't exist yet).

- [ ] **Step 3: Add the field to `GenerationRecord`**

In `backend/app/knowledge/failure_db.py`, change (currently lines 164-172):

```python
class GenerationRecord:
    idea:             str
    attempt_number:   int
    final_score:      float
    succeeded:        bool          # score >= DEPLOY_THRESHOLD
    fix_count:        int
    dominant_errors:  list[str]     # top error messages
    architecture_hash: str          # sha256 of project structure (for "similar apps" lookup)
    bundle_refs:      list = field(default_factory=list)  # [{"failure_id", "bundle_path"}, ...]
    timestamp:        str = ""
```

- [ ] **Step 4: Wire pipeline.py to populate it**

In `backend/app/core/pipeline.py`, change (currently lines 342-352):

```python
            generation_log.record(GenerationRecord(
                idea=ctx.idea[:200],
                attempt_number=len(ctx.fix_attempts),
                final_score=ctx.latest_score,
                succeeded=ctx.is_deployment_ready,
                fix_count=len(ctx.fix_attempts),
                dominant_errors=[d.message[:80] for d in all_diags
                                  if getattr(d, "severity", None) and
                                  d.severity.value in ("critical", "high")][:5],
                architecture_hash=arch_hash,
                bundle_refs=[d.metadata["bundle_ref"] for d in all_diags
                             if isinstance(getattr(d, "metadata", None), dict)
                             and d.metadata.get("bundle_ref")],
            ))
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && python tests/reliability/test_generation_record_bundle_refs.py`
Expected: `3/3 passed`

- [ ] **Step 6: Run the entire Task 1-4 test suite together**

Run:
```bash
cd backend
for f in tests/reliability/test_forensic_bundle.py tests/reliability/test_journey_evidence_capture.py tests/reliability/test_engine_bundle_wiring.py tests/reliability/test_generation_record_bundle_refs.py; do
  python "$f" || echo "FAILED: $f"
done
```
Expected: every file reports `N/N passed`, no `FAILED:` lines.

- [ ] **Step 7: Commit**

```bash
git add backend/app/knowledge/failure_db.py backend/app/core/pipeline.py backend/tests/reliability/test_generation_record_bundle_refs.py
git commit -m "Surface Forensic Bundle references in generation_log.jsonl"
```

---

### Task 5: Document the experiment

**Files:**
- Modify: `experiments.md` (repo root) — append a new entry after Experiment 037.

**Interfaces:** None (documentation only).

- [ ] **Step 1: Append the experiment entry**

At the end of `experiments.md`, add:

```markdown

## Experiment 038 — Forensic Bundle System (V20.1): generic, reusable failure evidence

**Hypothesis:** Measurement before medicine. `JourneyCRUDFailure` examples in
telemetry were truncated to 80-200 characters (`generation_log.jsonl`'s
`dominant_errors`, `patterns.json`'s `examples`) — real request/response
evidence was computed at failure time and then thrown away. Before building
any replay tooling, stop discarding the evidence, in a schema generic
enough for every future failure class (build/deploy/auth/vision), not
journey-specific.

**What shipped:**
- `app/memory/forensic_bundle.py` — a standalone, failure-class-agnostic
  bundle writer: `bundle_version`, `failure_id` (monotonic `FR-NNNNNN`),
  timestamp, `forgeai_version`, `pipeline_version`, `commit_sha`, project,
  provider/model/seed, `{stage, class, step}`, request/response, stderr,
  a `generation` metadata slot (category/style/layout/design
  fingerprint — nullable, not yet populated by any caller), and a reserved
  `artifacts` slot (screenshot/console_log/network_log/playwright_trace —
  null today, populated by V20.3 with no schema change).
- `user_journey_runner.py`'s `_ExchangeRecorder` captures the last HTTP
  request/response made by a failing step, without touching any of the
  11 step closures' return signatures.
- `engine.py`'s runtime stage writes a bundle on `JourneyCRUDFailure` and
  attaches `{failure_id, bundle_path}` to the diagnostic's metadata.
- `pipeline.py`'s existing (already-live) `generation_log.jsonl` write now
  carries `bundle_refs`, so `reliability_metrics.py`/`failure_report.py`
  and any future dashboard can resolve a failure straight to its full
  evidence file instead of an 80-character string.

**Verification ($0):** 13 new asserts across 4 test files (bundle schema +
monotonic IDs + auth redaction; recorder captures the right exchange and
never stores a raw Authorization header; engine.py writes a bundle only
when a failed step has evidence and attaches the ref; GenerationRecord
round-trips bundle_refs through the same json path generation_log.jsonl
already uses). No LLM calls, no canary run — this is a $0 telemetry
change with no effect on generation behavior or score.

**Explicitly deferred (per the user's stated order):** screenshots,
browser console/network logs, replay execution, and any dashboard/heatmap
UI. Those are V20.2 (Replay) / V20.3 (Browser Evidence) / V20.4 (Replay
Studio) — this cycle is only "stop throwing the evidence away."

**Next reliability target:** run a canary (`run_canary.py`) once ready to
spend credits, confirm a real `JourneyCRUDFailure` produces a populated
bundle file, then decide whether V20.2 (load a bundle, re-run the exact
request) is next. **Cost:** $0.
```

- [ ] **Step 2: Commit**

```bash
git add experiments.md
git commit -m "Document Experiment 038: Forensic Bundle System"
```

---

## Self-Review Notes

- **Spec coverage:** structured request/response ✅ (Task 2/3), failure IDs ✅ (Task 1), bundle paths in failure memory ✅ (Task 4, via the already-live `generation_log.jsonl` sink — not `patterns.json`, which was confirmed during investigation to not be called from the V15 pipeline path at all), generic schema (not journey-specific) ✅ (Task 1's `forensic_bundle.py` has zero journey/HTTP knowledge), metadata fields (commit SHA, ForgeAI version, pipeline version, provider/model, category/style/layout/design fingerprint slot) ✅ (Task 1 schema — category/style/layout/fingerprint are nullable and NOT populated by this cycle's single caller; wiring them from `app/design/brief.py`'s `DesignBrief` is a fast follow, not required for "working, testable" here). Screenshots/replay/heatmap ✅ explicitly excluded (Task 5's deferred list).
- **Placeholder scan:** no TBD/TODO; every step has complete code.
- **Type consistency:** `write_bundle()`'s keyword names match `_write_journey_bundle`'s call in Task 3; `GenerationRecord.bundle_refs` matches the list-of-dicts shape both `_write_journey_bundle` returns and Task 4's pipeline.py extraction produces.
