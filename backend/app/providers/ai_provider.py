import os
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Callable, Iterator

from app.providers.deepseek_provider import generate as deepseek_generate
from app.providers.cerebras_provider import generate as cerebras_generate
from app.providers.gemini_provider import generate as gemini_generate
from app.providers.gemini_provider import current_model as gemini_current_model
from app.providers.openrouter_provider import generate as openrouter_generate
from app.providers.groq_provider import generate as groq_generate
from app.providers.ollama_provider import generate as ollama_generate
from app.providers.openai_provider import DEFAULT_MODEL as openai_default_model
from app.providers.openai_provider import generate as openai_generate


# Providers currently returning 402 Payment Required (dead billing, not a
# transient rate limit) get benched for a cooldown instead of being retried
# on every single call. A 402 means the account itself is out of credits --
# retrying immediately can't ever succeed, so hitting it anyway just wastes
# the timeout window that could go to a provider that might actually work,
# and on a request with only 2 healthy providers left in the chain, that
# wasted attempt is the difference between success and "All providers
# failed". Module-level (resets on process restart) and 1 hour is long
# enough to stop hammering a dead account but short enough to self-recover
# if billing gets fixed without needing a redeploy.
_provider_cooldown_until: dict = {}
_COOLDOWN_SECONDS = 3600

# This observer is intentionally process-local and opt-in.  V15's spawned
# supervisor binds it around one child pipeline so it can report *physical*
# provider attempts without copying prompts, responses, exception text, or
# credentials across the process boundary.  ContextVar keeps concurrent
# in-process callers isolated too.
_provider_attempt_observer: ContextVar[Callable[[dict], None] | None] = ContextVar(
    "forge_provider_attempt_observer", default=None
)
_provider_attempt_count: ContextVar[int] = ContextVar("forge_provider_attempt_count", default=0)


@contextmanager
def observe_provider_attempts(observer: Callable[[dict], None]) -> Iterator[None]:
    """Temporarily receive sanitized physical-provider attempt events.

    Callers must bind this only where progress reporting is required.  The
    observer is best-effort: telemetry failures can never change generation
    or fallback behavior.
    """
    token = _provider_attempt_observer.set(observer)
    count_token = _provider_attempt_count.set(0)
    try:
        yield
    finally:
        _provider_attempt_count.reset(count_token)
        _provider_attempt_observer.reset(token)


def _emit_provider_attempt(stage: str, provider: str, attempt: int, status: str) -> None:
    """Emit the four allowlisted fields for one physical provider call."""
    observer = _provider_attempt_observer.get()
    if observer is None:
        return
    try:
        observer({
            "stage": str(stage),
            "provider": str(provider),
            "attempt": int(attempt),
            "status": status,
        })
    except Exception:
        pass


def _is_payment_required(exc: Exception) -> bool:
    if getattr(exc, "status_code", None) == 402:
        return True
    msg = str(exc).lower()
    if "402" in msg and ("payment" in msg or "quota" in msg or "billing" in msg):
        return True
    # Gemini reports a fully-depleted prepayment balance as HTTP 429
    # RESOURCE_EXHAUSTED, not 402 -- indistinguishable from a transient
    # rate limit by status code alone. Without this, every call retries it
    # 3x with a 5s backoff (auto_chain) before falling through, on an
    # account that literally cannot succeed until billing is fixed. Match
    # on the specific depleted-balance wording rather than "429" alone, so
    # an actual transient rate limit still gets its normal retries.
    if "resource_exhausted" in msg and ("credits are depleted" in msg or "prepayment" in msg):
        return True
    return False


def _note_provider_result(provider_name: str, exc: Exception) -> None:
    if _is_payment_required(exc):
        _provider_cooldown_until[provider_name] = time.time() + _COOLDOWN_SECONDS
        print(f"  [{provider_name}] 402 Payment Required — benching for {_COOLDOWN_SECONDS // 60} min")


def _on_cooldown(provider_name: str) -> bool:
    until = _provider_cooldown_until.get(provider_name)
    return bool(until and time.time() < until)


def _tracked(provider_name: str, model: str, prompt: str, fn, stage: str = "unknown", **kwargs) -> str:
    """Wrap one provider attempt with success/failure telemetry.

    The failure record deliberately contains provider metadata only.  Prompts
    can contain user data and generated credentials, so they must never be
    copied into operational telemetry.
    """
    # A ContextVar counter makes started/terminal events share an attempt
    # number without global state leaking between concurrent pipeline runs.
    attempt = _provider_attempt_count.get() + 1
    _provider_attempt_count.set(attempt)
    _emit_provider_attempt(stage, provider_name, attempt, "started")
    t0 = time.time()
    try:
        result = fn(prompt, **kwargs)
    except Exception as exc:
        elapsed = time.time() - t0
        try:
            from app.utils.cost_tracker import record_llm_failure
            record_llm_failure(stage, provider_name, model, elapsed, exc)
        except Exception:
            # Observability must never alter the provider fallback path.
            pass
        _emit_provider_attempt(stage, provider_name, attempt, "failed")
        raise

    elapsed = time.time() - t0
    try:
        from app.utils.cost_tracker import record_llm_call
        # Approximate token counts from string length (4 chars ≈ 1 token)
        prompt_tokens = len(prompt) // 4
        completion_tokens = len(result) // 4 if result else 0
        record_llm_call(stage, provider_name, model, prompt_tokens, completion_tokens, elapsed)
    except Exception:
        pass
    _emit_provider_attempt(stage, provider_name, attempt, "succeeded")
    return result


_GEMINI_RETRY_ATTEMPTS = 3
_GEMINI_RETRY_BACKOFF_SECONDS = 5
_CEREBRAS_FINAL_RETRY_BACKOFF_SECONDS = 15


def _auto_chain(prompt, stage, max_tokens, thinking_budget, skip: frozenset = frozenset(),
                 _cerebras_already_tried: bool = False):
    """
    OpenAI → Cerebras → Gemini (with retries) → Groq. OpenAI is the primary
    provider; the others are independent fallbacks for transient errors,
    rate limits, and billing outages.

    _cerebras_already_tried: set by generate_content's explicit
    provider=="cerebras" branch when ITS OWN try already failed with a
    transient error (not a 402 cooldown) before calling in here with
    skip={"cerebras"} to avoid an instant, pointless re-attempt of the
    identical call. Only suppresses the FIRST Cerebras leg below --
    the final-retry leg (after Gemini+Groq both also fail) still runs
    regardless, since that's the exact safety net Exp109 was built for
    (a transient Cerebras timeout, Gemini credit-depleted, Groq 413 on
    TPM) and skip={"cerebras"} used to wall it off entirely, silently
    disabling it for the single most common call path (model router
    defaults to explicit provider="cerebras", not "auto").
    """
    if "openai" not in skip and not _on_cooldown("openai"):
        try:
            print("Using OpenAI (main)")
            return _tracked("openai", openai_default_model, prompt, openai_generate, stage, max_tokens=max_tokens)
        except Exception as e:
            print(f"OpenAI failed: {e}")
            _note_provider_result("openai", e)

    if "cerebras" not in skip and not _cerebras_already_tried and not _on_cooldown("cerebras"):
        try:
            print("Using Cerebras (main)")
            return _tracked("cerebras", "gpt-oss-120b", prompt, cerebras_generate, stage, max_tokens=max_tokens)
        except Exception as e:
            print(f"Cerebras failed: {e}")
            _note_provider_result("cerebras", e)

    if "gemini" not in skip and not _on_cooldown("gemini"):
        last_exc: Exception | None = None
        for attempt in range(1, _GEMINI_RETRY_ATTEMPTS + 1):
            try:
                print(f"Using Gemini{'' if attempt == 1 else f' (retry {attempt}/{_GEMINI_RETRY_ATTEMPTS})'}")
                return _tracked("gemini", gemini_current_model(), prompt, gemini_generate, stage,
                                max_tokens=max_tokens, thinking_budget=thinking_budget)
            except Exception as e:
                last_exc = e
                print(f"Gemini failed: {e}")
                _note_provider_result("gemini", e)
                if _on_cooldown("gemini"):
                    break  # 402 — retrying can't help, stop immediately
                if attempt < _GEMINI_RETRY_ATTEMPTS:
                    time.sleep(_GEMINI_RETRY_BACKOFF_SECONDS)

    if "groq" not in skip and not _on_cooldown("groq"):
        try:
            print("Using Groq (fallback)")
            return _tracked("groq", "llama-3.3-70b", prompt, groq_generate, stage, max_tokens=max_tokens)
        except Exception as e:
            print(f"Groq failed: {e}")
            _note_provider_result("groq", e)

    # Final leg: ONE Cerebras retry after a short backoff. Confirmed live
    # (Exp109, exp109-milestone-r1): Cerebras failed with a transient
    # "Request timed out" while Gemini was credit-depleted and Groq 413'd
    # deterministically (12k TPM cap vs a ~14k-token fix prompt) — the
    # chain abandoned a call that Cerebras itself served fine seconds
    # later. Skipped when Cerebras is on cooldown (402) — retrying can't
    # help there — so genuinely-all-dead cases only pay one extra pause.
    if "cerebras" not in skip and not _on_cooldown("cerebras"):
        try:
            time.sleep(_CEREBRAS_FINAL_RETRY_BACKOFF_SECONDS)
            print("Using Cerebras (final retry — fallback legs failed)")
            return _tracked("cerebras", "gpt-oss-120b", prompt, cerebras_generate, stage, max_tokens=max_tokens)
        except Exception as e:
            print(f"Cerebras final retry failed: {e}")
            _note_provider_result("cerebras", e)

    raise RuntimeError("OpenAI, Cerebras, Gemini (after retries), and Groq all failed")


def generate_content(
    prompt,
    provider="auto",
    max_tokens=4000,
    stage: str = "unknown",
    thinking_budget: int = 0,
):
    # ── Global response cache ─────────────────────────────────────────────
    # Every call is cached by prompt hash, not just planner/architect. With a
    # cached architect output, the backend/frontend/fix prompts for a repeated
    # idea are byte-identical too — so a re-run of "todo app" costs $0 in
    # provider tokens instead of re-billing Gemini for ~40 calls. Identical
    # prompt == identical inputs, so serving the cached response is safe.
    # Disable with FORGE_LLM_CACHE=0.
    cache_enabled = os.environ.get("FORGE_LLM_CACHE", "1") != "0"
    if cache_enabled:
        try:
            from app.utils.llm_cache import get_cached, set_cached
            cache_payload = {"prompt": prompt, "max_tokens": max_tokens}
            cached = get_cached("llm", cache_payload)
            if cached and cached.get("response"):
                print(f"[LLM cache] HIT ({stage}) — 0 tokens billed")
                try:
                    from app.utils.cost_tracker import record_cache_hit
                    record_cache_hit(stage, len(prompt) // 4, len(cached["response"]) // 4)
                except Exception:
                    pass
                return cached["response"]
        except Exception:
            cache_enabled = False

    result = _generate_uncached(prompt, provider, max_tokens, stage, thinking_budget)

    if cache_enabled and result:
        try:
            set_cached("llm", cache_payload, {"response": result, "stage": stage})
        except Exception:
            pass
    return result


def _generate_uncached(
    prompt,
    provider="auto",
    max_tokens=4000,
    stage: str = "unknown",
    thinking_budget: int = 0,
):
    # Specific-provider requests (e.g. the model router picking "gemini" for a
    # whole pipeline run) used to have NO fallback: a single transient error
    # (rate limit, 503 high-demand, etc.) on that one provider would abort the
    # entire generation, discarding every stage that already succeeded. Now
    # each specific request falls back through the auto chain (skipping the
    # provider that just failed) instead of raising straight through.

    if provider == "deepseek":
        try:
            print("Using DeepSeek")
            return _tracked("deepseek", "deepseek-chat", prompt, deepseek_generate, stage, max_tokens=max_tokens)
        except Exception as e:
            print(f"DeepSeek failed ({e}) — falling back to auto chain")
            return _auto_chain(prompt, stage, max_tokens, thinking_budget)

    if provider == "cerebras":
        if _on_cooldown("cerebras"):
            print("Cerebras on cooldown from a recent 402 — going straight to auto chain")
            return _auto_chain(prompt, stage, max_tokens, thinking_budget, skip=frozenset({"cerebras"}))
        try:
            print("Using Cerebras")
            return _tracked("cerebras", "gpt-oss-120b", prompt, cerebras_generate, stage, max_tokens=max_tokens)
        except Exception as e:
            print(f"Cerebras failed ({e}) — falling back to auto chain")
            _note_provider_result("cerebras", e)
            # Don't skip cerebras outright -- a transient failure here (timeout,
            # brief 5xx) still leaves it eligible for _auto_chain's deliberate
            # final-retry leg once Gemini+Groq also fail. Only _cerebras_already_tried
            # suppresses the redundant immediate re-attempt; a real 402 cooldown
            # (handled above) is the only case that should skip cerebras entirely.
            return _auto_chain(prompt, stage, max_tokens, thinking_budget, _cerebras_already_tried=True)

    if provider == "groq":
        if _on_cooldown("groq"):
            print("Groq on cooldown from a recent 402 — going straight to auto chain")
            return _auto_chain(prompt, stage, max_tokens, thinking_budget, skip=frozenset({"groq"}))
        try:
            print("Using Groq")
            return _tracked("groq", "llama-3.3-70b", prompt, groq_generate, stage, max_tokens=max_tokens)
        except Exception as e:
            print(f"Groq failed ({e}) — falling back to auto chain")
            _note_provider_result("groq", e)
            return _auto_chain(prompt, stage, max_tokens, thinking_budget, skip=frozenset({"groq"}))

    if provider == "openrouter":
        try:
            print("Using OpenRouter")
            return _tracked("openrouter", "auto", prompt, openrouter_generate, stage, max_tokens=max_tokens)
        except Exception as e:
            print(f"OpenRouter failed ({e}) — falling back to auto chain")
            return _auto_chain(prompt, stage, max_tokens, thinking_budget)

    if provider == "gemini":
        if _on_cooldown("gemini"):
            print("Gemini on cooldown from a recent 402 — going straight to auto chain")
            return _auto_chain(prompt, stage, max_tokens, thinking_budget, skip=frozenset({"gemini"}))
        try:
            print("Using Gemini")
            return _tracked("gemini", gemini_current_model(), prompt, gemini_generate, stage,
                            max_tokens=max_tokens, thinking_budget=thinking_budget)
        except Exception as e:
            print(f"Gemini failed ({e}) — falling back to auto chain")
            _note_provider_result("gemini", e)
            return _auto_chain(prompt, stage, max_tokens, thinking_budget, skip=frozenset({"gemini"}))

    if provider == "openai":
        if _on_cooldown("openai"):
            return _auto_chain(prompt, stage, max_tokens, thinking_budget, skip=frozenset({"openai"}))
        try:
            print("Using OpenAI")
            return _tracked("openai", openai_default_model, prompt, openai_generate, stage, max_tokens=max_tokens)
        except Exception as e:
            print(f"OpenAI failed ({e}) — falling back to Cerebras chain")
            _note_provider_result("openai", e)
            # OpenAI was already attempted for this request. Skip its first
            # auto-chain leg so the next provider is Cerebras rather than
            # spending a second OpenAI attempt on the same failing call.
            return _auto_chain(
                prompt,
                stage,
                max_tokens,
                thinking_budget,
                skip=frozenset({"openai"}),
            )

    if provider == "ollama":
        print("Using Ollama")
        return _tracked("ollama", "local", prompt, ollama_generate, stage, max_tokens=max_tokens)

    # Auto mode
    return _auto_chain(prompt, stage, max_tokens, thinking_budget)
