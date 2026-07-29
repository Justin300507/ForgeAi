"""Bounded, Windows-spawn-safe execution for legacy ``/jobs`` V15 runs.

The child reads only its own persisted job input and sends a deliberately
small IPC protocol back to the parent.  The parent remains the only writer of
terminal SQLAlchemy job state, which makes cancellation win over late child
results.
"""
from __future__ import annotations

import datetime as _dt
import multiprocessing as mp
import os
import queue
import re
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit

_SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_SAFE_PROJECT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_SAFE_URL = re.compile(r"^https?://[^\s]{1,500}$", re.IGNORECASE)
DEFAULT_V15_JOB_DEADLINE_S = 20 * 60
_V15_CREDENTIAL_ENV_KEYS = frozenset({
    "GITHUB_TOKEN",
    "RAILWAY_TOKEN",
    "CLOUDFLARE_API_TOKEN",
    "CLOUDFLARE_ACCOUNT_ID",
})

# Windows' ``spawn`` child inherits the environment at ``Process.start()``.
# Keep that unavoidable compatibility bridge serialized and as short as
# possible: a request's credentials must never become ambient state for a
# different V15 background thread.
_SPAWN_ENV_LOCK = threading.RLock()


class V15JobDeadlineExceeded(RuntimeError):
    def __init__(self, stage: str, deadline_at: _dt.datetime):
        self.stage = stage
        self.deadline_at = deadline_at
        super().__init__("v15 job deadline exceeded")


class V15JobCancelled(RuntimeError):
    """The parent observed cancellation and terminated its owned child."""


@dataclass(frozen=True)
class V15SupervisorResult:
    result: dict[str, Any]
    last_stage: str
    selected_provider: str | None
    effective_provider: str | None


def v15_job_deadline_s(value: int | None = None) -> int:
    """Whole-job deadline is server configuration, never request input."""
    if value is not None:
        return max(1, int(value))
    try:
        return max(1, int(os.getenv("FORGE_V15_JOB_DEADLINE_S", str(DEFAULT_V15_JOB_DEADLINE_S))))
    except ValueError:
        return DEFAULT_V15_JOB_DEADLINE_S


def ensure_generation_job_columns(engine) -> None:
    """Apply the nullable, additive supervisor migration to an existing DB."""
    from sqlalchemy import inspect, text

    if not inspect(engine).has_table("generation_jobs"):
        return
    additions = {
        "progress_stage": "VARCHAR(64)",
        "progress_updated_at": "TIMESTAMP",
        "effective_provider": "VARCHAR(32)",
        "execution_token": "VARCHAR(64)",
        "lease_expires_at": "TIMESTAMP",
        "deadline_at": "TIMESTAMP",
        "total_tokens": "INTEGER",
        "estimated_cost_usd": "FLOAT",
        "cache_hits": "INTEGER",
    }
    # Each ALTER gets an independent transaction.  On PostgreSQL, a duplicate
    # column error aborts the *current* transaction; catching it inside one
    # shared transaction makes every later migration statement fail too.
    for name, sql_type in additions.items():
        existing = {c["name"] for c in inspect(engine).get_columns("generation_jobs")}
        if name in existing:
            continue
        try:
            with engine.begin() as conn:
                    conn.execute(text(f"ALTER TABLE generation_jobs ADD COLUMN {name} {sql_type}"))
        except Exception:
            # A second application process may have added the column after
            # inspection.  The failed transaction has already rolled back;
            # now a fresh inspection safely distinguishes that race from a
            # genuine migration failure.
            refreshed = {c["name"] for c in inspect(engine).get_columns("generation_jobs")}
            if name not in refreshed:
                raise


def _safe_identifier(value: object, default: str = "unknown") -> str:
    value = str(value or default).lower()
    return value if _SAFE_ID.fullmatch(value) else default


def _safe_url(value: object) -> str | None:
    """Keep deployment URLs useful without transporting secrets in query/fragment."""
    if not isinstance(value, str) or not _SAFE_URL.fullmatch(value):
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    # Credentials in userinfo are as sensitive as query tokens.  The child
    # protocol permits only a conventional HTTP(S) origin and path.
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return None
    if parsed.username is not None or parsed.password is not None:
        return None
    clean = urlunsplit((parsed.scheme.lower(), parsed.netloc, parsed.path, "", ""))
    return clean if _SAFE_URL.fullmatch(clean) else None


def _safe_options(options: dict[str, Any]) -> dict[str, Any]:
    """Keep the spawn payload metadata-only even for malformed API input."""
    style = options.get("style_override")
    style = _safe_identifier(style, default="") if style is not None else None
    motion = options.get("motion_intensity")
    motion = motion if motion in {"subtle", "moderate", "heavy"} else None
    return {
        "style_override": style or None,
        "motion_intensity": motion,
        "include_landing_page": bool(options.get("include_landing_page", False)),
    }


def _credential_overrides(overrides: dict[str, str] | None) -> dict[str, str]:
    """Copy only supported, non-empty deployment credentials for a V15 child."""
    if not overrides:
        return {}
    return {
        name: value
        for name, value in overrides.items()
        if name in _V15_CREDENTIAL_ENV_KEYS and isinstance(value, str) and value
    }


@contextmanager
def _spawn_environment(overrides: dict[str, str] | None):
    """Temporarily overlay a single Windows spawn snapshot and restore exactly."""
    snapshot = _credential_overrides(overrides)
    with _SPAWN_ENV_LOCK:
        original = {name: os.environ.get(name) for name in snapshot}
        try:
            os.environ.update(snapshot)
            yield
        finally:
            for name, value in original.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value


def _terminate_owned_process_tree(process, *, timeout_s: float = 5) -> None:
    """Stop only this supervisor's Windows child and descendants.

    ``Process.terminate`` stops only the direct child on Windows.  V15 may
    launch dev servers or package managers, so taskkill's explicit ``/T`` is
    required on deadline, cancellation, and final cleanup.  No port lookup or
    generic process selection is used: the PID comes solely from our Process.
    """
    pid = process.pid
    if not isinstance(pid, int) or pid <= 0:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=max(1, timeout_s),
            )
        except (OSError, subprocess.SubprocessError):
            # The process can naturally exit between the liveness check and
            # taskkill.  Joining below is still the correct cleanup path.
            pass
    else:
        # The production target is Windows.  Keep local non-Windows test
        # environments bounded without pretending to discover unrelated PIDs.
        try:
            process.terminate()
        except (OSError, ValueError):
            pass
    process.join(timeout=timeout_s)
    if process.is_alive() and hasattr(process, "kill"):
        try:
            process.kill()
        except (OSError, ValueError):
            pass
        process.join(timeout=timeout_s)


def _safe_zip_path(value: object, project_name: str | None) -> str | None:
    """Preserve only the conventional generated-project archive path."""
    if not isinstance(value, str) or not project_name:
        return None
    normalized = value.replace("\\", "/")
    if "?" in normalized or "#" in normalized or normalized.rsplit("/", 1)[-1] != f"{project_name}.zip":
        return None
    return f"generated_projects/{project_name}.zip"


def _safe_child_result(result: object) -> dict[str, Any]:
    """Allowlist result fields so child IPC cannot carry prompts or secrets."""
    if not isinstance(result, dict):
        return {"status": "error"}
    score = result.get("forge_score")
    if isinstance(score, dict):
        score = score.get("score")
    try:
        score = float(score) if score is not None else None
    except (TypeError, ValueError):
        score = None
    project_name = result.get("project_name")
    if not isinstance(project_name, str) or not _SAFE_PROJECT.fullmatch(project_name):
        project_name = None
    # Token counts and an estimated dollar amount are non-sensitive numeric
    # telemetry. Bound them before crossing the child-process IPC boundary.
    try:
        total_tokens = int(result.get("total_tokens", 0))
    except (TypeError, ValueError):
        total_tokens = 0
    try:
        estimated_cost = float(result.get("estimated_cost", 0.0))
    except (TypeError, ValueError):
        estimated_cost = 0.0
    total_tokens = total_tokens if 0 <= total_tokens <= 100_000_000 else 0
    estimated_cost = estimated_cost if 0.0 <= estimated_cost <= 10_000.0 else 0.0
    try:
        cache_hits = int(result.get("cache_hits", 0))
    except (TypeError, ValueError):
        cache_hits = 0
    cache_hits = cache_hits if 0 <= cache_hits <= 100_000 else 0
    deployment = result.get("deployment") if isinstance(result.get("deployment"), dict) else {}
    v6_result = result.get("v6_result") if isinstance(result.get("v6_result"), dict) else {}
    return {
        "status": "done" if result.get("status") == "done" else "error",
        "project_name": project_name,
        "forge_score": score,
        "total_tokens": total_tokens,
        "estimated_cost": round(estimated_cost, 8),
        "cache_hits": cache_hits,
        "deployed": bool(result.get("deployed")),
        "backend_url": _safe_url(result.get("backend_url") or deployment.get("backend_url")),
        "frontend_url": _safe_url(result.get("frontend_url") or deployment.get("frontend_url")),
        # V15 has no GitHub push; keep the legacy report shape without
        # transmitting its verbose pipeline result through IPC.
        "deployment": {
            "backend_url": _safe_url(deployment.get("backend_url")),
            "frontend_url": _safe_url(deployment.get("frontend_url")),
        },
        "v6_result": {"zip_path": _safe_zip_path(v6_result.get("zip_path"), project_name)},
    }


def _send(messages, payload: dict[str, Any]) -> None:
    try:
        messages.put_nowait(payload)
    except Exception:
        pass  # observability must never block a generation child


def _send_terminal(messages, payload: dict[str, Any]) -> None:
    """A result/error is not telemetry: give the draining parent a brief chance."""
    try:
        messages.put(payload, timeout=2)
    except Exception:
        pass


def _child_event(messages, payload: dict[str, Any]) -> None:
    event = payload.get("event")
    if event in {"stage:start", "stage:end"}:
        _send(messages, {"type": "stage", "stage": _safe_identifier(payload.get("stage"))})
    elif event == "provider:attempt":
        attempt = payload.get("attempt")
        if not isinstance(attempt, int) or attempt < 1 or attempt > 10_000:
            return
        status = payload.get("status")
        if status not in {"started", "succeeded", "failed"}:
            return
        # This is deliberately a four-field protocol.  Do not add model,
        # prompts, responses, exception text, timing, or arbitrary metadata.
        _send(messages, {
            "type": "provider_attempt",
            "stage": _safe_identifier(payload.get("stage")),
            "provider": _safe_identifier(payload.get("provider")),
            "attempt": attempt,
            "status": status,
        })


class _ChildLogTee:
    """Relays this child's stdout to the parent as best-effort progress text.

    V14's ``_TeeStdout`` (main.py) captures prints via a thread-local list
    because it runs generation in-thread.  V15 runs generation in this
    separate child process, so its prints never reached the parent at all
    -- the frontend's "Generation log" panel stayed at 0 lines for every
    V15 job, success or failure, not just intermittently.  This still
    writes through to the child's own real stdout unchanged (container
    logs, and this file's temporary diagnostic print below, are
    unaffected); it additionally best-effort relays each line through the
    same non-blocking ``_send`` used for stage/provider_attempt events, so
    a full queue or IPC hiccup drops a progress line rather than the
    generation itself.
    """

    def __init__(self, real_stdout, messages, secrets: frozenset[str]):
        self._real_stdout = real_stdout
        self._messages = messages
        self._secrets = secrets

    def write(self, text: str) -> None:
        self._real_stdout.write(text)
        line = text.rstrip("\n") if text else ""
        if not line.strip():
            return
        if any(secret and secret in line for secret in self._secrets):
            return
        _send(self._messages, {"type": "log", "line": line[:2000]})

    def flush(self) -> None:
        self._real_stdout.flush()


def _child_log_secrets() -> frozenset[str]:
    """Values that must never be relayed verbatim, even accidentally."""
    keys = (
        "OPENAI_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY", "CEREBRAS_API_KEY",
        "OPENROUTER_API_KEY", "DEEPSEEK_API_KEY", "SECRET_KEY",
        *_V15_CREDENTIAL_ENV_KEYS,
    )
    return frozenset(v for k in keys if (v := os.environ.get(k)))


def _run_v15_child(job_id: str, options: dict[str, Any], messages, pipeline_runner=None) -> None:
    """Spawn/fork target.  It does no terminal job-state writing."""
    real_stdout = sys.stdout
    try:
        if pipeline_runner is not None:
            result = pipeline_runner(job_id, options, messages)
            _send_terminal(messages, {"type": "result", "result": _safe_child_result(result)})
            return

        # The prompt is read from the job row inside the child, rather than
        # serialized over IPC.  Only the job id and non-sensitive options are
        # arguments to multiprocessing's transport.
        from app.database import engine as _inherited_engine
        if os.name != "nt":
            # On the fork() path (see run_v15_supervisor), this child's copy
            # of app.database's module-level `engine` -- and any pooled
            # connections it already opened in the PARENT before fork -- was
            # duplicated by the OS fork, not freshly created. SQLite (this
            # app's default) does not support sharing a connection across
            # processes; using an inherited one here risks "database is
            # locked" or silent corruption if the parent touches the same
            # connection concurrently. dispose(close=False) is SQLAlchemy's
            # documented post-fork pattern: drop the inherited pool
            # references without attempting to close them (the parent still
            # owns and may still be using them), and lazily open brand-new
            # connections for everything this child does from here on. A
            # no-op under spawn (Windows): the child there is a fresh
            # interpreter that hasn't created any connections yet.
            _inherited_engine.dispose(close=False)

        from app.database import SessionLocal
        from app.models.generation_job import GenerationJob
        from app.core.events import EventBus, Events
        from app.providers.ai_provider import observe_provider_attempts
        from app.providers.model_router import route as model_route
        from app.services.v15_orchestrator import generate_project_v15

        db = SessionLocal()
        try:
            job = db.query(GenerationJob).filter(GenerationJob.id == job_id).first()
            if job is None:
                raise LookupError("GenerationJob")
            idea, provider = job.idea, job.provider or "auto"
            deploy_to = job.deploy_to or "none"
        finally:
            db.close()

        # This is the router's *selection*, not a claim about a later fallback
        # provider actually servicing an API call.  Attempt telemetry is not
        # available at this process boundary, so preserve that distinction.
        selected_provider = _safe_identifier(model_route(idea, stage="planning", provider=provider).provider)
        _send(messages, {"type": "selected_provider", "provider": selected_provider})
        bus = EventBus().on("*", lambda payload: _child_event(messages, payload))
        # Binding occurs only inside this spawned V15 child.  The callback is
        # synchronous but _child_event uses put_nowait, so progress cannot
        # hold up provider fallback or generation.
        # Relay this child's own generation prints as best-effort progress
        # text (see _ChildLogTee) -- restored before the except block below
        # ever prints anything, so exception bodies/tracebacks are never
        # candidates for relay, matching the original IPC-minimalism intent.
        sys.stdout = _ChildLogTee(real_stdout, messages, _child_log_secrets())
        try:
            with observe_provider_attempts(
                lambda attempt: bus.emit(Events.PROVIDER_ATTEMPT, attempt)
            ):
                result = generate_project_v15(
                    idea=idea, provider=selected_provider,
                    deploy=deploy_to != "none",
                    deploy_to=deploy_to if deploy_to != "none" else "vercel",
                    job_id=job_id,
                    style_override=options.get("style_override"),
                    motion_intensity=options.get("motion_intensity"),
                    include_landing_page=bool(options.get("include_landing_page", False)),
                    event_bus=bus,
                )
        finally:
            sys.stdout = real_stdout
        _send_terminal(messages, {"type": "result", "result": _safe_child_result(result)})
    except BaseException as exc:
        # Exception bodies can echo prompts, credentials, or JWTs.  Only the
        # exception class crosses the process boundary.  Restore real stdout
        # first (redundant if the try above's finally already ran, but this
        # is the safety net for exceptions raised before that point) so
        # nothing printed from here on is a candidate for relay.
        sys.stdout = real_stdout
        # TEMPORARY DIAGNOSTIC (2026-07-23): print the real traceback to this
        # child's own stdout (server-side log only, never crosses the IPC
        # boundary above) to root-cause a RuntimeError appearing after
        # successful pipeline completion under fork() on Railway. Revert once
        # root-caused.
        import traceback
        print("[DIAGNOSTIC] _run_v15_child real exception:", flush=True)
        traceback.print_exc()
        _send_terminal(messages, {"type": "error", "error_type": _safe_identifier(type(exc).__name__)})
    finally:
        sys.stdout = real_stdout


def run_v15_supervisor(
    job_id: str,
    *,
    options: dict[str, Any],
    on_event: Callable[[str, str | dict[str, Any] | None], None],
    is_cancelled: Callable[[], bool] | None = None,
    deadline_s: int | None = None,
    credential_overrides: dict[str, str] | None = None,
    pipeline_runner=None,
) -> V15SupervisorResult:
    """Run one V15 child and surface safe progress to the parent thread."""
    # "spawn" is the only option on Windows (this repo's dev environment),
    # which is why this module's docstring calls it out by name -- but
    # ForgeAI's actual PRODUCTION target is a Linux container (Render), not
    # Windows, and this file's own os.name=="nt" branches elsewhere already
    # acknowledge that split. spawn starts a brand-new interpreter per job
    # and re-imports this app's full dependency chain (every deterministic
    # patcher, every provider SDK, SQLAlchemy, ...) from scratch, on top of
    # the already-running parent server's own copy of the same. On Render's
    # free-tier 512MB instance this is expensive enough to OOM-kill the
    # whole service (confirmed live, 2026-07-22, right after this module
    # shipped) -- fork() is copy-on-write against the parent's already-
    # loaded memory and costs a small fraction of that per job. The
    # documented risk of fork alongside a threaded server (a lock held by
    # another thread at fork time staying locked forever in the child) is
    # mitigated here: the child never touches the parent's asyncio loop,
    # HTTP listener, or in-process locks -- it opens its own fresh DB
    # session and its own fresh HTTP calls -- and _run_v15_child disposes
    # any inherited DB connection pool as its very first action.
    ctx = mp.get_context("spawn" if os.name == "nt" else "fork")
    messages = ctx.Queue(maxsize=64)
    process = ctx.Process(
        target=_run_v15_child,
        args=(job_id, _safe_options(options), messages, pipeline_runner),
        daemon=False,
    )
    seconds = v15_job_deadline_s(deadline_s)
    started = time.monotonic()
    deadline_at = _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(seconds=seconds)
    last_stage, selected_provider, effective_provider = "queued", None, None
    # Do not mutate the long-lived server environment outside this lock-scoped
    # spawn operation.  The child receives the requested snapshot; sibling
    # V15 jobs cannot observe it or inherit it accidentally.
    with _spawn_environment(credential_overrides):
        process.start()
    try:
        while True:
            if is_cancelled and is_cancelled():
                _terminate_owned_process_tree(process)
                raise V15JobCancelled("job cancelled")
            remaining = seconds - (time.monotonic() - started)
            if remaining <= 0:
                _terminate_owned_process_tree(process)
                raise V15JobDeadlineExceeded(last_stage, deadline_at)
            try:
                message = messages.get(timeout=min(0.5, remaining))
            except queue.Empty:
                on_event("heartbeat", None)
                if not process.is_alive():
                    raise RuntimeError("pipeline child exited without a result")
                continue
            kind = message.get("type")
            if kind == "stage":
                last_stage = _safe_identifier(message.get("stage"))
                on_event("stage", last_stage)
            elif kind == "selected_provider":
                selected_provider = _safe_identifier(message.get("provider"))
                on_event("selected_provider", selected_provider)
            elif kind == "log":
                line = message.get("line")
                if isinstance(line, str) and line:
                    on_event("log", line[:2000])
            elif kind == "provider_attempt":
                attempt = message.get("attempt")
                status = message.get("status")
                provider = _safe_identifier(message.get("provider"))
                stage = _safe_identifier(message.get("stage"))
                if (
                    isinstance(attempt, int)
                    and 1 <= attempt <= 10_000
                    and status in {"started", "succeeded", "failed"}
                    and provider != "unknown"
                ):
                    safe_attempt = {
                        "stage": stage,
                        "provider": provider,
                        "attempt": attempt,
                        "status": status,
                    }
                    on_event("provider_attempt", safe_attempt)
                    if status == "succeeded":
                        effective_provider = provider
            elif kind == "result":
                result = message.get("result")
                if not isinstance(result, dict):
                    raise RuntimeError("pipeline child returned an invalid result")
                return V15SupervisorResult(
                    result=result,
                    last_stage=last_stage,
                    selected_provider=selected_provider,
                    effective_provider=effective_provider,
                )
            elif kind == "error":
                raise RuntimeError(f"pipeline_child_error:{_safe_identifier(message.get('error_type'))}")
    finally:
        _terminate_owned_process_tree(process)
        messages.close()
