import os
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Callable, Iterator

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


_OPENAI_RETRY_ATTEMPTS = 3
_OPENAI_RETRY_BACKOFF_SECONDS = 5


def _auto_chain(prompt, stage, max_tokens, thinking_budget):
    """
    OpenAI only, with retries on transient errors.

    Cerebras/Gemini/Groq were removed as fallback legs at the user's
    explicit request (2026-07-30): none had usable credits/keys on
    Railway (Cerebras 402 payment-required, Gemini key unset, Groq key
    invalid), so every call that hit this chain paid the full latency of
    three guaranteed-dead providers (Gemini's own 3x-retry backoff alone
    added 10-15s) before ultimately failing anyway -- turning a single
    transient OpenAI timeout into a total generation failure. This was
    the actual root cause behind the "RuntimeError after successful
    pipeline completion" investigated at length elsewhere in
    experiments.md: there was no process/fork() bug, just every fallback
    leg being dead and the resulting total failure surfacing as an
    opaque, generically-labeled RuntimeError. Retrying OpenAI directly
    is both faster and strictly more likely to succeed than that dead
    chain ever was.
    """
    last_exc: Exception | None = None
    for attempt in range(1, _OPENAI_RETRY_ATTEMPTS + 1):
        if _on_cooldown("openai"):
            break
        try:
            print(f"Using OpenAI{' (main)' if attempt == 1 else f' (retry {attempt}/{_OPENAI_RETRY_ATTEMPTS})'}")
            return _tracked("openai", openai_default_model, prompt, openai_generate, stage, max_tokens=max_tokens)
        except Exception as e:
            last_exc = e
            print(f"OpenAI failed: {e}")
            _note_provider_result("openai", e)
            if _on_cooldown("openai"):
                break  # 402 — retrying can't help, stop immediately
            if attempt < _OPENAI_RETRY_ATTEMPTS:
                time.sleep(_OPENAI_RETRY_BACKOFF_SECONDS)

    raise RuntimeError(f"OpenAI failed after {_OPENAI_RETRY_ATTEMPTS} attempts: {last_exc}")


# Exp157 (habit_tracker, 2026-07-31): this cache's own rationale --
# "identical prompt == identical inputs, so serving the cached response
# is safe" -- holds for the INITIAL generation stages (planner,
# architect, backend/frontend generation: same idea always should
# produce the same output) but not for the repair loop. A "fix"/
# "architecture_fix"/"runtime_fix" prompt is sent specifically BECAUSE a
# previous attempt failed, and the retry/escalation strategies
# (patch_file -> enriched patch -> regenerate_module -> switch_model)
# exist on the premise that a later attempt gets a genuinely fresh shot
# -- switch_model's own cache key doesn't even include which model was
# selected. Confirmed live: a "Frontend/browser failure" fix prompt
# produced a broken useAuth.jsx/AuthContext.jsx pair (neither exported a
# usable `useAuth`), got cached unconditionally by this function before
# the regression it caused was even detected, and was replayed via
# `[LLM cache] HIT` on a LATER attempt even after Exp156's FixCache
# eviction correctly cleared the separate, higher-level diagnostic-hash
# cache for the exact same group -- confirming this is a genuinely
# distinct caching layer, not just FixCache under another name. Repair-
# loop stages are exempted entirely rather than added to Exp156's
# regression-triggered eviction, since eviction there would need the
# exact prompt threaded back up through several call layers that
# currently only return file content, not what prompt produced it.
_NEVER_CACHE_STAGES = frozenset({"fix", "architecture_fix", "runtime_fix"})


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
    # prompt == identical inputs, so serving the cached response is safe --
    # EXCEPT for repair-loop stages, see _NEVER_CACHE_STAGES above.
    # Disable with FORGE_LLM_CACHE=0.
    cache_enabled = (
        os.environ.get("FORGE_LLM_CACHE", "1") != "0"
        and stage not in _NEVER_CACHE_STAGES
    )
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
    # Cerebras/Gemini/Groq/DeepSeek/OpenRouter have no usable credits or
    # keys configured (see _auto_chain's docstring) -- an explicit request
    # for any of them goes straight to the OpenAI-only chain instead of
    # paying for a guaranteed-failing attempt first.
    if provider in ("deepseek", "cerebras", "groq", "openrouter", "gemini"):
        print(f"{provider} has no configured credits — using OpenAI instead")
        return _auto_chain(prompt, stage, max_tokens, thinking_budget)

    if provider == "openai":
        # _auto_chain IS the OpenAI-with-retries policy now -- no separate
        # single-attempt path needed here.
        return _auto_chain(prompt, stage, max_tokens, thinking_budget)

    if provider == "ollama":
        print("Using Ollama")
        return _tracked("ollama", "local", prompt, ollama_generate, stage, max_tokens=max_tokens)

    # Auto mode
    return _auto_chain(prompt, stage, max_tokens, thinking_budget)
