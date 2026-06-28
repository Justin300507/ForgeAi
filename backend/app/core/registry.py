"""
PluginRegistry — modular stage and verifier registration.

Allows adding new verifiers, score dimensions, or fix strategies
without modifying any existing pipeline code.

Usage:
    from app.core.registry import registry

    # Register a custom verifier
    @registry.verifier("env_check")
    def check_env_vars(ctx: GenerationContext) -> VerificationResult:
        ...

    # Register a custom score dimension
    @registry.dimension
    def my_custom_dim(ctx: GenerationContext) -> ScoreDimension:
        ...

    # Build a V15 pipeline with all registered plugins loaded
    pipeline = registry.build_pipeline()
"""
from __future__ import annotations

from typing import Callable, Optional, Any


class PluginRegistry:
    """
    Central registry for V15 pipeline plugins.

    All registered plugins are applied when calling build_pipeline(),
    which returns a fully-configured V15Pipeline instance.
    """

    def __init__(self):
        self._verifiers:   dict[str, Callable]   = {}
        self._dimensions:  list[Callable]         = []
        self._fix_hooks:   list[Callable]         = []   # pre/post-fix hooks
        self._stage_hooks: dict[str, list[Callable]] = {}

    # ── Decorators ────────────────────────────────────────────────────────────

    def verifier(self, name: str):
        """Register a static verifier function: fn(ctx) → VerificationResult"""
        def decorator(fn: Callable) -> Callable:
            self._verifiers[name] = fn
            return fn
        return decorator

    def dimension(self, fn: Callable) -> Callable:
        """Register a score dimension function: fn(ctx) → ScoreDimension"""
        self._dimensions.append(fn)
        return fn

    def on_fix(self, fn: Callable) -> Callable:
        """Register a hook called before each fix attempt: fn(ctx, cfg) → None"""
        self._fix_hooks.append(fn)
        return fn

    def on_stage(self, stage_name: str):
        """Register a hook called when a named stage completes: fn(ctx, result) → None"""
        def decorator(fn: Callable) -> Callable:
            self._stage_hooks.setdefault(stage_name, []).append(fn)
            return fn
        return decorator

    # ── Pipeline factory ──────────────────────────────────────────────────────

    def build_pipeline(
        self,
        run_runtime: bool = True,
        run_browser: bool = True,
        deploy: bool = True,
        deploy_to: str = "both",
    ) -> Any:
        """
        Return a fully-configured V15Pipeline with all registered plugins.
        This is the recommended way to instantiate the pipeline.
        """
        from app.verification.engine import VerificationEngine
        from app.scoring.engine import ScoringEngine
        from app.repair.orchestrator import FixOrchestrator
        from app.retry.manager import RetryManager
        from app.core.pipeline import V15Pipeline

        ve = VerificationEngine(run_runtime=run_runtime, run_browser=run_browser)
        for name, fn in self._verifiers.items():
            ve.register(fn)

        se = ScoringEngine()
        for dim_fn in self._dimensions:
            se.add_dimension(dim_fn)

        fo = FixOrchestrator(ve, se)
        rm = RetryManager(max_attempts=5)

        return V15Pipeline(
            verification_engine = ve,
            scoring_engine      = se,
            fix_orchestrator    = fo,
            retry_manager       = rm,
            deploy              = deploy,
            deploy_to           = deploy_to,
        )

    def list_plugins(self) -> dict:
        return {
            "verifiers":  list(self._verifiers.keys()),
            "dimensions": [fn.__name__ for fn in self._dimensions],
            "fix_hooks":  [fn.__name__ for fn in self._fix_hooks],
        }


# Global singleton — import and use directly
registry = PluginRegistry()
