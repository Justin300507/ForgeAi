import time

from app.providers.deepseek_provider import generate as deepseek_generate
from app.providers.cerebras_provider import generate as cerebras_generate
from app.providers.gemini_provider import generate as gemini_generate
from app.providers.openrouter_provider import generate as openrouter_generate
from app.providers.groq_provider import generate as groq_generate
from app.providers.ollama_provider import generate as ollama_generate
from app.providers.openai_provider import generate as openai_generate


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
    Cerebras → Groq → Gemini → Ollama. Free-tier providers first: Gemini is
    the only one here that bills per token, so it is the paid fallback, not
    the default (it was burning the entire Gemini credit balance on every
    auto call). OpenRouter/DeepSeek removed (out of credits — a 402 wastes a
    slot then falls through to DeepSeek, which writes partial code and makes
    errors worse). `skip` excludes a provider a caller already tried
    directly, so we don't retry the same failure.
    """
    if "cerebras" not in skip:
        try:
            print("Using Cerebras")
            return _tracked("cerebras", "gpt-oss-120b", prompt, cerebras_generate, stage, max_tokens=max_tokens)
        except Exception as e:
            print(f"Cerebras failed: {e}")

    if "groq" not in skip:
        try:
            print("Using Groq (fallback)")
            return _tracked("groq", "llama-3.3-70b", prompt, groq_generate, stage, max_tokens=max_tokens)
        except Exception as e:
            print(f"Groq failed: {e}")

    if "gemini" not in skip:
        try:
            print("Using Gemini (paid fallback)")
            return _tracked("gemini", "gemini-2.5-flash", prompt, gemini_generate, stage,
                            max_tokens=max_tokens, thinking_budget=thinking_budget)
        except Exception as e:
            print(f"Gemini failed: {e}")

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
        try:
            print("Using Cerebras")
            return _tracked("cerebras", "gpt-oss-120b", prompt, cerebras_generate, stage, max_tokens=max_tokens)
        except Exception as e:
            print(f"Cerebras failed ({e}) — falling back to auto chain")
            return _auto_chain(prompt, stage, max_tokens, thinking_budget, skip=frozenset({"cerebras"}))

    if provider == "groq":
        try:
            print("Using Groq")
            return _tracked("groq", "llama-3.3-70b", prompt, groq_generate, stage, max_tokens=max_tokens)
        except Exception as e:
            print(f"Groq failed ({e}) — falling back to auto chain")
            return _auto_chain(prompt, stage, max_tokens, thinking_budget, skip=frozenset({"groq"}))

    if provider == "openrouter":
        try:
            print("Using OpenRouter")
            return _tracked("openrouter", "auto", prompt, openrouter_generate, stage, max_tokens=max_tokens)
        except Exception as e:
            print(f"OpenRouter failed ({e}) — falling back to auto chain")
            return _auto_chain(prompt, stage, max_tokens, thinking_budget)

    if provider == "gemini":
        try:
            print("Using Gemini")
            return _tracked("gemini", "gemini-2.5-flash", prompt, gemini_generate, stage,
                            max_tokens=max_tokens, thinking_budget=thinking_budget)
        except Exception as e:
            print(f"Gemini failed ({e}) — falling back to auto chain")
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