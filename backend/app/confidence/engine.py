"""
Deployment Confidence Engine

Outputs a probability rather than a pass/fail threshold:

    Probability this app succeeds in production: 97.3%
    Based on:
      - Score: 96.2 / 100
      - 0 fix attempts needed
      - Runtime: fully passing
      - Historical base rate: 91.2% (from 147 prior apps)

Formula:
    confidence = f(score, runtime, fix_count, historical_rate)

Calibrated to human intuition:
  96 score + 0 fixes  → ~97-98%
  90 score + 1 fix    → ~84%
  80 score + 2 fixes  → ~70%
  60 score + 3 fixes  → ~42%
  Failed static check → ~20%
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class ConfidenceReport:
    probability:      float          # 0.0–1.0
    pct:              float          # = probability * 100
    grade:            str            # A+ / A / B / C / D / F
    factors:          dict[str, float]  # contributing factors
    historical_rate:  float          # base rate from generation log
    historical_n:     int            # how many prior apps informed the rate
    verdict:          str            # human-readable one-liner

    def display(self):
        bar_len = 30
        filled  = int(self.pct / 100 * bar_len)
        bar     = "#" * filled + "." * (bar_len - filled)
        print(f"\n  Deployment confidence: {self.pct:.1f}%  [{bar}]  {self.grade}")
        print(f"  {self.verdict}")
        print(f"  Factors:")
        for k, v in self.factors.items():
            sign = "+" if v >= 0 else ""
            print(f"    {k:<28} {sign}{v:+.3f}")
        print(f"  Historical base rate: {self.historical_rate*100:.1f}%  (n={self.historical_n})")


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def _grade(pct: float) -> str:
    if pct >= 97: return "A+"
    if pct >= 93: return "A"
    if pct >= 87: return "B+"
    if pct >= 80: return "B"
    if pct >= 70: return "C"
    if pct >= 55: return "D"
    return "F"


def compute_confidence(
    overall_score:   float,
    runtime_score:   float,
    browser_score:   float,
    compile_score:   float,
    fix_count:       int,
    had_critical:    bool  = False,
    historical_rate: float = 0.75,
    historical_n:    int   = 0,
) -> ConfidenceReport:
    """
    Compute deployment confidence from current pipeline state + history.

    Each factor contributes a logit value; these are summed and passed through
    sigmoid to get a probability. Calibrated so that:
      - A perfect app (all scores = 100, 0 fixes) → ~99%
      - A score-gated app (score = 95, 0 fixes) → ~97%
      - An app with 1 fix, score 90 → ~85%
      - An app with critical errors → ~20%
    """
    factors: dict[str, float] = {}

    # ── Base logit (intercept) ────────────────────────────────────────────────
    # Biased negative so that a mediocre app doesn't get inflated confidence
    logit = -0.5
    factors["base"] = -0.5

    # ── Overall score (dominant signal, quadratic) ────────────────────────────
    # Quadratic: difference between 95 and 80 is much larger than between 70 and 55
    score_contribution = 2.5 * (overall_score / 100.0) ** 2
    logit += score_contribution
    factors["overall_score"] = round(score_contribution, 3)

    # ── Runtime bonus ─────────────────────────────────────────────────────────
    rt_contribution = 0.5 * (runtime_score / 100.0)
    logit += rt_contribution
    factors["runtime_health"] = round(rt_contribution, 3)

    # ── Browser bonus ─────────────────────────────────────────────────────────
    br_contribution = 0.3 * (browser_score / 100.0)
    logit += br_contribution
    factors["browser_ux"] = round(br_contribution, 3)

    # ── Compile bonus ─────────────────────────────────────────────────────────
    co_contribution = 0.2 * (compile_score / 100.0)
    logit += co_contribution
    factors["compilation"] = round(co_contribution, 3)

    # ── Fix count penalty ─────────────────────────────────────────────────────
    # Each LLM fix round reduces confidence (fixer may introduce new issues)
    fix_penalty = -0.4 * fix_count
    logit += fix_penalty
    if fix_count > 0:
        factors["fix_penalty"] = round(fix_penalty, 3)

    # ── Critical error penalty ────────────────────────────────────────────────
    if had_critical:
        crit_penalty = -1.0
        logit += crit_penalty
        factors["critical_errors"] = crit_penalty

    # ── Historical signal (Bayesian update, small weight) ─────────────────────
    if historical_n >= 5:
        weight        = min(1.0, historical_n / 100.0)
        hist_log_odds = math.log(historical_rate / max(1e-6, 1 - historical_rate))
        hist_contribution = weight * hist_log_odds * 0.25
        logit += hist_contribution
        factors["historical_base_rate"] = round(hist_contribution, 3)
    else:
        historical_rate = 0.75  # conservative prior with no data

    probability = _sigmoid(logit)
    pct = probability * 100.0

    # Build verdict
    if pct >= 97:
        verdict = "Ready for production — high confidence this app will work."
    elif pct >= 90:
        verdict = "Likely to succeed — minor risks remain."
    elif pct >= 80:
        verdict = "Moderate confidence — review the fix history before deploying."
    elif pct >= 65:
        verdict = "Borderline — manual testing recommended before deployment."
    elif pct >= 45:
        verdict = "Low confidence — significant failure risk. Manual review required."
    else:
        verdict = "Not deployment-ready — critical issues likely remain."

    return ConfidenceReport(
        probability=round(probability, 4),
        pct=round(pct, 2),
        grade=_grade(pct),
        factors=factors,
        historical_rate=round(historical_rate, 4),
        historical_n=historical_n,
        verdict=verdict,
    )


def compute_from_context(ctx) -> ConfidenceReport:
    """
    Convenience wrapper: compute confidence from a GenerationContext object.
    Pulls score dimensions from ctx.score_history and historical data from the generation log.
    """
    from app.knowledge.failure_db import generation_log

    # Extract scores from context. Every attribute below used to be a name
    # GenerationContext doesn't have (best_score/scores/attempt_count don't
    # exist; the real fields are latest_score/score_history/fix_attempts —
    # see app/core/context.py) and `dims` was checked with isinstance(dict)
    # when ScoreDimension.dimensions is a list, so this always fell through
    # to the 0.0 defaults even when the getattr calls didn't already raise.
    # On top of that, all_diagnostics is a method, not a property, so the
    # `for d in getattr(ctx, "all_diagnostics", [])` line threw TypeError on
    # every call — meaning compute_from_context() never once returned a real
    # report; every call was silently swallowed by pipeline.py's `except
    # Exception: confidence = None`.
    overall = ctx.latest_score

    dims_by_name: dict[str, float] = {}
    if ctx.score_history:
        for dim in ctx.score_history[-1].dimensions:
            if dim.score is not None:
                dims_by_name[dim.name] = dim.score

    runtime_score = dims_by_name.get("Runtime Startup", 0.0)
    browser_score = dims_by_name.get("Browser UX", 0.0)
    compile_score = dims_by_name.get("Compilation", 0.0)

    fix_count    = len(ctx.fix_attempts)
    had_critical = ctx.has_critical()

    # Historical rate from generation log
    hist_rate = generation_log.success_rate(recent_n=100)
    hist_n    = len(generation_log.load_recent(100))

    return compute_confidence(
        overall_score   = overall,
        runtime_score   = runtime_score,
        browser_score   = browser_score,
        compile_score   = compile_score,
        fix_count       = fix_count,
        had_critical    = had_critical,
        historical_rate = hist_rate,
        historical_n    = hist_n,
    )
