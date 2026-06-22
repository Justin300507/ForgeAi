import time

from app.providers.gemini_provider import generate as gemini_generate
from app.providers.openrouter_provider import generate as openrouter_generate
from app.providers.groq_provider import generate as groq_generate
from app.providers.ollama_provider import generate as ollama_generate
from app.providers.cerebras_provider import generate as cerebras_generate
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


def generate_content(
    prompt,
    provider="auto",
    max_tokens=4000,
    stage: str = "unknown",
):

    if provider == "cerebras":
        print("Using Cerebras")
        return _tracked("cerebras", "gpt-oss-120b", prompt, cerebras_generate, stage, max_tokens=max_tokens)

    if provider == "groq":
        print("Using Groq")
        return _tracked("groq", "llama-3.3-70b", prompt, groq_generate, stage, max_tokens=max_tokens)

    if provider == "openrouter":
        print("Using OpenRouter")
        return _tracked("openrouter", "auto", prompt, openrouter_generate, stage, max_tokens=max_tokens)

    if provider == "gemini":
        print("Using Gemini")
        return _tracked("gemini", "gemini-2.5-flash", prompt, gemini_generate, stage, max_tokens=max_tokens)

    if provider == "openai":
        print("Using OpenAI")
        return _tracked("openai", "gpt-4o-mini", prompt, openai_generate, stage, max_tokens=max_tokens)

    if provider == "ollama":
        print("Using Ollama")
        return _tracked("ollama", "local", prompt, ollama_generate, stage, max_tokens=max_tokens)

    # Auto mode: Cerebras -> Groq -> OpenRouter -> Gemini -> Ollama

    try:
        print("Using Cerebras")
        return _tracked("cerebras", "gpt-oss-120b", prompt, cerebras_generate, stage, max_tokens=max_tokens)
    except Exception as e:
        print(f"Cerebras failed: {e}")

    try:
        print("Using Groq")
        return _tracked("groq", "llama-3.3-70b", prompt, groq_generate, stage, max_tokens=max_tokens)
    except Exception as e:
        print(f"Groq failed: {e}")

    try:
        print("Using OpenRouter")
        return _tracked("openrouter", "auto", prompt, openrouter_generate, stage, max_tokens=max_tokens)
    except Exception as e:
        print(f"OpenRouter failed: {e}")

    try:
        print("Using OpenAI")
        return _tracked("openai", "gpt-4o-mini", prompt, openai_generate, stage, max_tokens=max_tokens)
    except Exception as e:
        print(f"OpenAI failed: {e}")

    try:
        print("Using Gemini")
        return _tracked("gemini", "gemini-2.5-flash", prompt, gemini_generate, stage, max_tokens=max_tokens)
    except Exception as e:
        print(f"Gemini failed: {e}")

    try:
        print("Using Ollama (final fallback)")
        return _tracked("ollama", "local", prompt, ollama_generate, stage, max_tokens=max_tokens)
    except Exception as e:
        print(f"Ollama failed: {e}")

    raise RuntimeError("All providers failed")