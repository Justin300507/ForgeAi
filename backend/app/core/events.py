"""
EventBus — lightweight pub/sub for pipeline observability.

Stages emit events; UI/logging handlers subscribe.
Keeps stage logic decoupled from logging/streaming concerns.
"""
from __future__ import annotations

import threading
from typing import Callable, Any


class EventBus:
    """
    Thread-safe synchronous event bus.
    Handlers are called inline (no background threads) to keep ordering deterministic.
    """

    def __init__(self):
        self._handlers: dict[str, list[Callable]] = {}
        self._lock = threading.Lock()

    def on(self, event: str, handler: Callable[[dict], None]) -> "EventBus":
        """Subscribe handler to event type. Returns self for chaining."""
        with self._lock:
            self._handlers.setdefault(event, []).append(handler)
        return self

    def off(self, event: str, handler: Callable) -> "EventBus":
        with self._lock:
            if event in self._handlers:
                self._handlers[event] = [h for h in self._handlers[event] if h is not handler]
        return self

    def emit(self, event: str, payload: dict[str, Any] | None = None) -> None:
        payload = payload or {}
        with self._lock:
            handlers = list(self._handlers.get(event, []) + self._handlers.get("*", []))
        for h in handlers:
            try:
                h({"event": event, **payload})
            except Exception:
                pass  # handler errors never crash the pipeline


# Canonical event names
class Events:
    STAGE_START    = "stage:start"
    STAGE_END      = "stage:end"
    STAGE_LOG      = "stage:log"
    FIX_ATTEMPT    = "fix:attempt"
    FIX_DONE       = "fix:done"
    SCORE_UPDATE   = "score:update"
    DEPLOY_START   = "deploy:start"
    DEPLOY_DONE    = "deploy:done"
    REGRESSION     = "regression:detected"
    PROVIDER_ATTEMPT = "provider:attempt"
    PIPELINE_DONE  = "pipeline:done"


# Global default bus — stages import this if they don't receive an injected one
_default_bus = EventBus()


def get_default_bus() -> EventBus:
    return _default_bus


def log_handler(payload: dict) -> None:
    """Default handler: print pipeline events to stdout."""
    ev = payload.get("event", "")
    if ev == Events.STAGE_START:
        print(f"\n[V15] >> {payload.get('stage','?')} ...")
    elif ev == Events.STAGE_END:
        status = payload.get("status", "?")
        ms = payload.get("duration_ms", 0)
        icon = "OK" if status == "passed" else ("FAIL" if status == "failed" else "->")
        print(f"[V15] {icon} {payload.get('stage','?')} [{status}] ({ms:.0f}ms)")
    elif ev == Events.STAGE_LOG:
        print(f"  {payload.get('message','')}")
    elif ev == Events.FIX_ATTEMPT:
        print(f"\n[V15] Fix attempt {payload.get('attempt','?')}/5 -- strategy: {payload.get('strategy','?')}")
    elif ev == Events.SCORE_UPDATE:
        score = payload.get("score", 0)
        grade = payload.get("grade", "?")
        ready = payload.get("deployment_ready", False)
        tag = "[DEPLOY READY]" if ready else "[needs repair]"
        print(f"[V15] Score: {score:.1f} ({grade}) -- {tag}")
    elif ev == Events.REGRESSION:
        print(f"[V15] WARNING: Regression detected -- reverting fix")
    elif ev == Events.PIPELINE_DONE:
        print(f"\n[V15] === Pipeline complete: {payload.get('status','?')} ===")
