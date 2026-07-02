"""
RetryManager — 5-level escalating retry strategy.

Attempt  Strategy               Model           When
───────  ─────────────────────  ──────────────  ─────────────────────────────────
  1      PATCH_FILE             current         First pass: targeted line fixes
  2      PATCH_FILE (improved)  current         Richer prompt with full context
  3      REGENERATE_MODULE      current         Full module rewrite
  4      PATCH_FILE             backup/stronger Switch to a more capable model
  5      REGENERATE_ARCH        strongest       Architecture-level redesign

Each attempt returns a StrategyConfig that the FixOrchestrator executes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from app.core.context import FixStrategy, GenerationContext


@dataclass
class StrategyConfig:
    attempt:          int
    strategy:         FixStrategy
    provider:         str
    model_hint:       str          # e.g. "strongest", "current", "backup"
    improve_prompt:   bool = False  # inject extra context/examples into fix prompt
    regen_arch:       bool = False  # redesign the architecture before regenerating
    description:      str = ""


_ESCALATION_PLAN: list[StrategyConfig] = [
    StrategyConfig(
        attempt=1,
        strategy=FixStrategy.PATCH_FILE,
        provider="auto",
        model_hint="current",
        improve_prompt=False,
        description="Patch affected files with current model",
    ),
    StrategyConfig(
        attempt=2,
        strategy=FixStrategy.PATCH_FILE,
        provider="auto",
        model_hint="current",
        improve_prompt=True,
        description="Patch with enriched prompt (full file context + examples)",
    ),
    StrategyConfig(
        attempt=3,
        strategy=FixStrategy.REGENERATE_MODULE,
        provider="auto",
        model_hint="current",
        improve_prompt=True,
        description="Regenerate entire affected module (not just changed lines)",
    ),
    StrategyConfig(
        attempt=4,
        strategy=FixStrategy.SWITCH_MODEL,
        # NOT a hardcoded provider: OpenRouter is out of credits (see
        # ai_provider.py's auto chain, which already excludes it for that
        # reason) and Groq's free tier hits its daily quota fast. "auto" lets
        # the live fallback chain (Cerebras -> Groq -> Gemini -> Ollama) pick
        # whichever provider actually has room, instead of always calling a
        # provider already known to fail.
        provider="auto",
        model_hint="strongest",
        improve_prompt=True,
        description="Switch to strongest available model and patch",
    ),
    StrategyConfig(
        attempt=5,
        strategy=FixStrategy.REGENERATE_ARCH,
        provider="auto",
        model_hint="strongest",
        improve_prompt=True,
        regen_arch=True,
        description="Redesign architecture from scratch (nuclear option)",
    ),
]


class RetryManager:
    """
    Tracks fix attempts and returns the correct StrategyConfig per attempt.

    Usage:
        rm = RetryManager()
        while not score_ok:
            cfg = rm.next_strategy(ctx)
            if cfg is None:
                break   # exhausted all attempts
            fix_orchestrator.run(ctx, cfg)
    """

    def __init__(self, max_attempts: int = 5):
        self.max_attempts = max_attempts
        self._attempt     = 0
        self._history: list[tuple[StrategyConfig, float, float]] = []
                             # (config, score_before, score_after)

    @property
    def attempt_number(self) -> int:
        return self._attempt

    @property
    def exhausted(self) -> bool:
        return self._attempt >= self.max_attempts

    def next_strategy(self, ctx: GenerationContext) -> Optional[StrategyConfig]:
        """
        Return the next StrategyConfig, or None if all attempts are used.
        Also rotates the provider on attempt 4.
        """
        if self.exhausted:
            return None

        self._attempt += 1
        idx = min(self._attempt - 1, len(_ESCALATION_PLAN) - 1)
        cfg = _ESCALATION_PLAN[idx]

        # Attempt 4+: switch provider if the current strategy demands it
        if cfg.strategy == FixStrategy.SWITCH_MODEL:
            ctx.next_provider()
            cfg = StrategyConfig(
                **{**cfg.__dict__, "provider": ctx.current_provider}
            )

        print(f"\n[retry] Attempt {self._attempt}/{self.max_attempts}: {cfg.description}")
        return cfg

    def record_result(self, cfg: StrategyConfig, score_before: float, score_after: float):
        """Record the outcome of a fix attempt for diagnostic purposes."""
        self._history.append((cfg, score_before, score_after))
        delta = score_after - score_before
        icon  = "↑" if delta > 0 else ("↓" if delta < 0 else "→")
        print(f"[retry] Result: {score_before:.1f} {icon} {score_after:.1f} "
              f"(Δ={delta:+.1f})")

    def best_score(self) -> float:
        if not self._history:
            return 0.0
        return max(after for _, _, after in self._history)

    def summary(self) -> list[dict]:
        return [
            {
                "attempt":      i + 1,
                "strategy":     cfg.strategy.value,
                "provider":     cfg.provider,
                "score_before": before,
                "score_after":  after,
                "delta":        round(after - before, 2),
            }
            for i, (cfg, before, after) in enumerate(self._history)
        ]
