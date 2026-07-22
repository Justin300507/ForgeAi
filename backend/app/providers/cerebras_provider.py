import os

from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

client = OpenAI(
    api_key=os.getenv("CEREBRAS_API_KEY", "not-configured"),
    base_url="https://api.cerebras.ai/v1",
    # Large-output calls (frontend/backend generation, max_completion_tokens
    # up to 14000) routinely take 25-35s even on a clean run (confirmed live:
    # a successful control-test frontend generation took 29.4s) -- 20s left
    # no margin at all and risked spurious timeouts on any request that's
    # merely a bit larger or a bit slower than average, not actually stuck.
    timeout=60
)

DEFAULT_MODEL = "gpt-oss-120b"
# Large generation gets exactly one provider request before ai_provider moves
# to its independent fallback chain.  Keep this value in the executable tests
# so a future retry loop cannot silently reintroduce multi-minute stalls.
_MAX_LARGE_GENERATION_ATTEMPTS = 1


def generate(
    prompt: str,
    max_tokens: int = 4000,
    model: str = DEFAULT_MODEL
):
    # For large-output tasks (backend/frontend generation), keep reasoning low
    # so completion budget remains available for generated application code.
    use_low_reasoning = max_tokens >= 8000

    kwargs = dict(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_completion_tokens=max_tokens,
        temperature=0.2,
    )
    # Cerebras documents reasoning_effort for GPT-OSS through its OpenAI-
    # compatible client.  One documented request is both cheaper and bounded;
    # failures propagate immediately to ai_provider's provider fallback.
    if use_low_reasoning:
        response = client.chat.completions.create(**kwargs, reasoning_effort="low")
    else:
        response = client.chat.completions.create(**kwargs)

    # Token logging
    if hasattr(response, "usage") and response.usage:
        usage = response.usage

        reasoning_tokens = 0

        if (
            hasattr(usage, "completion_tokens_details")
            and usage.completion_tokens_details
        ):
            reasoning_tokens = (
                usage.completion_tokens_details.reasoning_tokens
                or 0
            )

        print(
            f"[CEREBRAS] "
            f"Prompt={usage.prompt_tokens} "
            f"Completion={usage.completion_tokens} "
            f"Reasoning={reasoning_tokens} "
            f"Total={usage.total_tokens}"
        )

    return response.choices[0].message.content
