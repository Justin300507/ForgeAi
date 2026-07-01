"""
Smart Model Router

Routes generation and fix tasks to the most cost-effective model
based on task complexity. Uses cheaper/faster models when the task
doesn't need maximum capability, and reserves strong models for
complex architectures and hard-to-fix bugs.

Complexity scoring:
  - Simple apps (todo, notes, basic CRUD): 0-2 → gemini (cheapest)
  - Medium apps (auth, dashboard, search): 3-5 → groq/cerebras (balanced)
  - Complex apps (real-time, payments, RBAC): 6-9 → cerebras/openrouter
  - Enterprise (multi-tenant, analytics, AI): 10+ → openrouter (strongest)

The router also tracks cost per provider/stage so it can learn
which model delivers the best quality-per-dollar over time.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


# ── Complexity signal dictionaries ─────────────────────────────────────────────

# Each keyword adds to the complexity score
_SIGNALS: dict[str, int] = {
    # +3: hard architectural features
    "multi-tenant":         3,
    "multitenant":          3,
    "rbac":                 3,
    "role based":           3,
    "real-time":            3,
    "realtime":             3,
    "websocket":            3,
    "payment":              3,
    "stripe":               3,
    "billing":              3,
    "subscription":         3,
    "oauth":                3,
    "sso":                  3,
    "saml":                 3,
    "elasticsearch":        3,
    "recommendation":       3,
    "machine learning":     3,
    "ml model":             3,
    "blockchain":           3,
    "microservice":         3,
    "enterprise":           3,
    # +2: moderately complex features
    "analytics":            2,
    "reporting":            2,
    "dashboard":            2,
    "notification":         2,
    "saas":                 2,
    "email":                2,
    "upload":               2,
    "file":                 2,
    "image":                2,
    "search":               2,
    "filter":               2,
    "permission":           2,
    "audit":                2,
    "workflow":             2,
    "approval":             2,
    "api key":              2,
    "rate limit":           2,
    "cache":                2,
    "redis":                2,
    "celery":               2,
    "background":           2,
    "scheduled":            2,
    "pdf":                  2,
    "excel":                2,
    "export":               2,
    "checkout":             2,
    # +1: basic features that add some complexity
    "auth":                 1,
    "login":                1,
    "register":             1,
    "jwt":                  1,
    "profile":              1,
    "settings":             1,
    "pagination":           1,
    "sort":                 1,
    "comment":              1,
    "tag":                  1,
    "category":             1,
    "statistics":           1,
    "history":              1,
    "reminder":             1,
    "cart":                 1,
    "order":                1,
    "import":               1,
    # -1: simple
    "todo":                -1,
    "note":                -1,
    "simple":              -1,
    "basic":               -1,
    "minimal":             -1,
    "small":               -1,
    "lightweight":         -1,
}

# Stage-specific model preferences (stage → complexity_tier → provider)
_STAGE_ROUTES: dict[str, dict[str, str]] = {
    "planning": {
        "low":        "gemini",     # Fast + free for simple apps
        "medium":     "groq",       # Free tier, fast
        "high":       "groq",       # Free tier
        "enterprise": "openrouter", # Strongest for enterprise
    },
    "architecture": {
        "low":        "groq",
        "medium":     "groq",
        "high":       "groq",
        "enterprise": "openrouter",
    },
    "backend": {
        "low":        "gemini",
        "medium":     "groq",
        "high":       "groq",
        "enterprise": "openrouter",
    },
    "frontend": {
        "low":        "gemini",
        "medium":     "groq",
        "high":       "groq",
        "enterprise": "openrouter",
    },
    "fix_simple": {
        "low":        "gemini",     # import errors, syntax: cheap model enough
        "medium":     "gemini",
        "high":       "groq",
        "enterprise": "groq",
    },
    "fix_complex": {
        "low":        "groq",       # logic bugs, auth, DB: need more capability
        "medium":     "groq",
        "high":       "groq",
        "enterprise": "openrouter",
    },
    "fix_nuclear": {
        "low":        "groq",       # full regen: groq is fast + free
        "medium":     "groq",
        "high":       "openrouter",
        "enterprise": "openrouter",
    },
}


@dataclass
class RoutingDecision:
    provider:         str
    complexity_score: int
    complexity_tier:  str   # "low" | "medium" | "high" | "enterprise"
    signals:          dict[str, int]   # which keywords triggered
    reasoning:        str


def score_complexity(idea: str) -> tuple[int, dict[str, int]]:
    """
    Compute a complexity integer score for a user idea.
    Returns (score, {signal: contribution}).
    """
    idea_lower = idea.lower()
    total = 0
    triggered: dict[str, int] = {}
    for keyword, weight in _SIGNALS.items():
        if keyword in idea_lower:
            total += weight
            triggered[keyword] = weight

    # Word count bonus: longer ideas usually have more requirements
    word_count = len(idea_lower.split())
    if word_count > 80:
        total += 2
        triggered["long_description"] = 2
    elif word_count > 40:
        total += 1
        triggered["medium_description"] = 1

    # Entity count heuristic: comma-separated "with X, Y, Z" patterns
    entities_match = re.search(r"with\s+[\w\s,]+", idea_lower)
    if entities_match:
        feature_count = entities_match.group().count(",")
        if feature_count >= 4:
            total += 2
            triggered["many_features"] = 2
        elif feature_count >= 2:
            total += 1
            triggered["several_features"] = 1

    return max(0, total), triggered


def tier_from_score(score: int) -> str:
    if score <= 2:   return "low"
    if score <= 5:   return "medium"
    if score <= 9:   return "high"
    return "enterprise"


def route(
    idea:     str,
    stage:    str = "backend",
    provider: str = "auto",
) -> RoutingDecision:
    """
    Determine which provider to use for a given task.

    Args:
        idea:     The original user idea (used for complexity scoring)
        stage:    Which pipeline stage this is (planning/architecture/backend/
                  frontend/fix_simple/fix_complex/fix_nuclear)
        provider: User's requested provider ("auto" = let the router decide)

    Returns:
        RoutingDecision with the selected provider and reasoning
    """
    # If user explicitly chose a provider, respect it
    if provider not in ("auto", ""):
        return RoutingDecision(
            provider=provider,
            complexity_score=0,
            complexity_tier="user_override",
            signals={},
            reasoning=f"User explicitly requested {provider}",
        )

    score, signals = score_complexity(idea)
    tier = tier_from_score(score)

    stage_map = _STAGE_ROUTES.get(stage, _STAGE_ROUTES["backend"])
    chosen_provider = stage_map.get(tier, "groq")

    reasoning = (
        f"complexity={score} ({tier}) "
        f"triggered=[{', '.join(list(signals.keys())[:5])}] "
        f"stage={stage} → {chosen_provider}"
    )

    return RoutingDecision(
        provider=chosen_provider,
        complexity_score=score,
        complexity_tier=tier,
        signals=signals,
        reasoning=reasoning,
    )


def route_for_fix(
    idea: str,
    fix_strategy: str,
    provider: str = "auto",
) -> RoutingDecision:
    """
    Route a fix call. Uses cheaper models for simple fixes (syntax/imports),
    stronger models for complex logic bugs and full regeneration.
    """
    stage_map = {
        "PATCH_FILE":          "fix_simple",
        "REGENERATE_FILE":     "fix_complex",
        "REGENERATE_MODULE":   "fix_complex",
        "SWITCH_MODEL":        "fix_complex",
        "REGENERATE_ARCH":     "fix_nuclear",
    }
    stage = stage_map.get(fix_strategy, "fix_complex")
    return route(idea, stage=stage, provider=provider)


# ── Cost estimator ─────────────────────────────────────────────────────────────
# Gemini 2.5 Flash: $0.15/1M input, $0.60/1M output (thinking disabled)
# Groq free tier: $0.00 (rate-limited, not billed)
_COST_PER_M_TOKENS: dict[str, float] = {
    "gemini":      0.38,   # Gemini 2.5 Flash avg ($0.15 in / $0.60 out, ~60% input)
    "groq":        0.00,   # Groq free tier
    "cerebras":    0.60,   # Cerebras gpt-oss-120b
    "openrouter":  3.00,   # premium model via OpenRouter
    "deepseek":    0.27,
    "openai":      2.50,
    "ollama":      0.00,
}


def estimate_cost_usd(provider: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Rough USD cost estimate for a provider call."""
    rate = _COST_PER_M_TOKENS.get(provider, 1.0)
    total_tokens = prompt_tokens + completion_tokens
    return (total_tokens / 1_000_000) * rate


# ── Data-driven provider learning ─────────────────────────────────────────────

import json as _json
from pathlib import Path as _Path

_PERF_PATH = _Path(__file__).parent.parent.parent / "failure_memory" / "provider_perf.json"


def _load_perf() -> dict:
    if _PERF_PATH.exists():
        try:
            return _json.loads(_PERF_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_perf(data: dict) -> None:
    try:
        _PERF_PATH.parent.mkdir(parents=True, exist_ok=True)
        _PERF_PATH.write_text(_json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass


def record_provider_outcome(
    idea: str,
    provider: str,
    forge_score: float,
    runtime_success: bool,
) -> None:
    """
    Record provider performance for an idea after the run completes.
    Over time this builds a data-driven map of:
      category × provider → avg score + success rate

    This feeds back into route() so the router becomes data-driven,
    not just rule-based.
    """
    if provider in ("auto", ""):
        return

    try:
        from app.knowledge.arch_db import classify_idea
        category = classify_idea(idea)
    except Exception:
        category = "generic"

    key = f"{category}::{provider}"
    data = _load_perf()
    entry = data.setdefault(key, {
        "category": category, "provider": provider,
        "seen": 0, "successes": 0, "total_score": 0.0,
    })
    entry["seen"] += 1
    entry["total_score"] += forge_score
    if runtime_success and forge_score >= 80:
        entry["successes"] += 1
    entry["avg_score"] = round(entry["total_score"] / entry["seen"], 1)
    entry["success_rate"] = round(entry["successes"] / entry["seen"], 3)
    _save_perf(data)


def get_best_provider_for_category(category: str, stage: str = "backend") -> Optional[str]:
    """
    Return the data-driven best provider for a category+stage,
    if we have ≥5 data points. Otherwise returns None (use rule-based routing).
    """
    data = _load_perf()
    candidates = {
        k: v for k, v in data.items()
        if v.get("category") == category and v.get("seen", 0) >= 5
    }
    if not candidates:
        return None

    # Pick highest average score
    best_key = max(candidates, key=lambda k: candidates[k].get("avg_score", 0))
    return candidates[best_key].get("provider")


def get_provider_leaderboard() -> list[dict]:
    """Return provider performance sorted by avg score (best first)."""
    data = _load_perf()
    rows = list(data.values())
    rows.sort(key=lambda r: r.get("avg_score", 0), reverse=True)
    return rows
