import os

from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

client = OpenAI(
    api_key=os.getenv("CEREBRAS_API_KEY", "not-configured"),
    base_url="https://api.cerebras.ai/v1",
    timeout=120
)

DEFAULT_MODEL = "gpt-oss-120b"


def generate(
    prompt: str,
    max_tokens: int = 4000,
    model: str = DEFAULT_MODEL
):
    # For large-output tasks (backend/frontend generation), disable reasoning to
    # avoid ~60% of the token budget being consumed by thinking tokens.
    # Use temperature=1 which bypasses thinking on Cerebras reasoning models.
    # For small-output tasks (fixes, critic), reasoning is fine and stays on.
    use_no_reasoning = max_tokens >= 8000

    kwargs = dict(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_completion_tokens=max_tokens,
        temperature=1 if use_no_reasoning else 0.2,
    )
    # Attempt to disable reasoning (thinking) for large-output tasks.
    # Try several known Cerebras thinking-disable parameter formats.
    if use_no_reasoning:
        response = None
        for extra in [
            {"thinking": {"type": "disabled"}},           # format A
            {"thinking": {"budget_tokens": 0}},           # format B
            {"budget_tokens": 0},                         # format C (top-level)
        ]:
            try:
                response = client.chat.completions.create(**kwargs, extra_body=extra)
                # Check if reasoning was actually reduced
                if (hasattr(response, "usage") and response.usage
                        and hasattr(response.usage, "completion_tokens_details")
                        and response.usage.completion_tokens_details):
                    r = response.usage.completion_tokens_details.reasoning_tokens or 0
                    # If still high reasoning AND hit the cap, retry with next format
                    if r > kwargs["max_completion_tokens"] * 0.4 and response.usage.completion_tokens == kwargs["max_completion_tokens"]:
                        continue
                break
            except Exception:
                continue
        if response is None:
            response = client.chat.completions.create(**kwargs)
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