import os
import time

from app.providers.deepseek_provider import generate as deepseek_generate
from app.providers.cerebras_provider import generate as cerebras_generate
from app.providers.gemini_provider import generate as gemini_generate
from app.providers.openrouter_provider import generate as openrouter_generate
from app.providers.groq_provider import generate as groq_generate
from app.providers.ollama_provider import generate as ollama_generate
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


def _is_payment_required(exc: Exception) -> bool:
    if getattr(exc, "status_code", None) == 402:
        return True
    msg = str(exc).lower()
    return "402" in msg and ("payment" in msg or "quota" in msg or "billing" in msg)


def _note_provider_result(provider_name: str, exc: Exception) -> None:
    if _is_payment_required(exc):
        _provider_cooldown_until[provider_name] = time.time() + _COOLDOWN_SECONDS
        print(f"  [{provider_name}] 402 Payment Required — benching for {_COOLDOWN_SECONDS // 60} min")


def _on_cooldown(provider_name: str) -> bool:
    until = _provider_cooldown_until.get(provider_name)
    return bool(until and time.time() < until)


def _tracked(provider_name: str, model: str, prompt: str, fn, stage: str = "unknown", **kwargs) -> str:
    """Wrap a provider call with cost tracking."""
    t0 = time.time()
    result = fn(prompt, **kwargs)
    elapsed = time.time() - t0
    try:
        from app.utils.cost_tracker import record_llm_call
        # Approximate token counts from string length (4 chars ≈ 1 token)
        prompt_tokens = len(prompt) // 4
        completion_tokens = len(result) // 4 if result else 0
        record_llm_call(stage, provider_name, model, prompt_tokens, completion_tokens, elapsed)
    except Exception:
        pass
    return result


def _auto_chain(prompt, stage, max_tokens, thinking_budget, skip: frozenset = frozenset()):
    """
    Gemini → Cerebras → Groq → Ollama. Gemini is the primary (user choice —
    quality); its per-token cost is offset by the response cache in
    generate_content, which short-circuits every repeated prompt before any
    provider is called. Cerebras/Groq are the free-tier fallbacks. OpenRouter/
    DeepSeek removed (out of credits — a 402 wastes a slot then falls through
    to DeepSeek, which writes partial code and makes errors worse). `skip`
    excludes a provider a caller already tried directly, so we don't retry
    the same failure.
    """
    if "gemini" not in skip and not _on_cooldown("gemini"):
        try:
            print("Using Gemini")
            return _tracked("gemini", "gemini-2.5-flash", prompt, gemini_generate, stage,
                            max_tokens=max_tokens, thinking_budget=thinking_budget)
        except Exception as e:
            print(f"Gemini failed: {e}")
            _note_provider_result("gemini", e)

    if "cerebras" not in skip and not _on_cooldown("cerebras"):
        try:
            print("Using Cerebras (fallback)")
            return _tracked("cerebras", "gpt-oss-120b", prompt, cerebras_generate, stage, max_tokens=max_tokens)
        except Exception as e:
            print(f"Cerebras failed: {e}")
            _note_provider_result("cerebras", e)
    elif "cerebras" not in skip:
        print("Skipping Cerebras (on cooldown from a recent 402)")

    if "groq" not in skip and not _on_cooldown("groq"):
        try:
            print("Using Groq (fallback)")
            return _tracked("groq", "llama-3.3-70b", prompt, groq_generate, stage, max_tokens=max_tokens)
        except Exception as e:
            print(f"Groq failed: {e}")
            _note_provider_result("groq", e)

    try:
        print("Using Ollama (offline fallback)")
        return _tracked("ollama", "local", prompt, ollama_generate, stage, max_tokens=max_tokens)
    except Exception as e:
        print(f"Ollama failed: {e}")

    raise RuntimeError("All providers failed")


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
            return _auto_chain(prompt, stage, max_tokens, thinking_budget, skip=frozenset({"cerebras"}))

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
            return _tracked("gemini", "gemini-2.5-flash", prompt, gemini_generate, stage,
                            max_tokens=max_tokens, thinking_budget=thinking_budget)
        except Exception as e:
            print(f"Gemini failed ({e}) — falling back to auto chain")
            _note_provider_result("gemini", e)
            return _auto_chain(prompt, stage, max_tokens, thinking_budget, skip=frozenset({"gemini"}))

    if provider == "openai":
        try:
            print("Using OpenAI")
            return _tracked("openai", "gpt-4o-mini", prompt, openai_generate, stage, max_tokens=max_tokens)
        except Exception as e:
            print(f"OpenAI failed ({e}) — falling back to auto chain")
            return _auto_chain(prompt, stage, max_tokens, thinking_budget)

    if provider == "ollama":
        print("Using Ollama")
        return _tracked("ollama", "local", prompt, ollama_generate, stage, max_tokens=max_tokens)

    # Auto mode
    return _auto_chain(prompt, stage, max_tokens, thinking_budget)