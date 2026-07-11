"""
Exp053 (Repair Pipeline Consolidation), Task 3: a single registry pattern
for "run a list of independent repair functions against a project,
recording what each one did and isolating failures between them."

This module is a DESIGN, built and tested standalone -- it does NOT
replace any of the four dispatch mechanisms documented in
docs/REPAIR_ARCHITECTURE.md yet. Migrating `run_deterministic_patches`'s
~40-call sequential list (or `run_frontend_patches`'s 14-call list) to
use this would be a real behavior-surface change to the live generation
pipeline, and this experiment has no way to validate that end-to-end
tonight (offline only, no API access, no live generation). Preflight.py's
own `PreflightRegistry` already independently implements the same pattern
(priority-ordered, per-fix try/except) and was not touched -- this module
generalizes that proven shape rather than replacing it, so a future
migration cycle has a tested target to move toward incrementally, one
dispatch mechanism at a time, each validated by its own canary run.

Design goals, matching what preflight.py already gets right:
- Deterministic, explicit ordering (priority number OR registration
  order -- both supported, priority wins when set).
- Per-entry failure isolation: one repair raising must never stop the
  rest (the same property Exp053 Task 6 added to
  run_deterministic_patches's own sequential list via a narrower,
  lower-risk _run_patch_isolated wrapper -- that fix shipped; this
  registry is the generalized version of the same idea for future use).
- A single call-shape: every registered repair takes the project path
  (plus optional extra args) and returns a count (or None), matching the
  `fn(root) or 0` convention every other dispatch mechanism already uses,
  so migrating an existing patcher list means changing the call site, not
  every individual patcher function's signature.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class _RepairEntry:
    name: str
    fn: Callable[..., Any]
    priority: int


class RepairRegistry:
    """
    RepairRegistry.register(name, fn, priority=N) then
    RepairRegistry.run(*args, **kwargs) -> dict[name, count].

    Entries run in ascending priority order (ties broken by registration
    order, stable). Each entry's exception is caught, logged, and recorded
    as a 0 count; every other entry still runs.
    """

    def __init__(self, label: str = "repair"):
        self.label = label
        self._entries: list[_RepairEntry] = []

    def register(self, name: str, fn: Callable[..., Any] | None = None, *, priority: int = 100):
        """
        Use as a decorator (`@registry.register("name", priority=10)`) or
        called directly (`registry.register("name", some_fn, priority=10)`).
        """
        if fn is not None:
            self._entries.append(_RepairEntry(name, fn, priority))
            return fn

        def _decorator(f: Callable[..., Any]) -> Callable[..., Any]:
            self._entries.append(_RepairEntry(name, f, priority))
            return f
        return _decorator

    def ordered_names(self) -> list[str]:
        """The exact execution order .run() will use -- exposed so a
        migration can assert it matches the sequence being replaced
        before switching a live call site over."""
        return [e.name for e in sorted(self._entries, key=lambda e: e.priority)]

    def run(self, *args, **kwargs) -> dict[str, int]:
        counts: dict[str, int] = {}
        for entry in sorted(self._entries, key=lambda e: e.priority):
            try:
                counts[entry.name] = entry.fn(*args, **kwargs) or 0
            except Exception as exc:
                counts[entry.name] = 0
                print(f"  [{self.label}] {entry.name} raised {type(exc).__name__}: {exc} "
                      f"-- skipping, continuing with remaining repairs")
        return counts
