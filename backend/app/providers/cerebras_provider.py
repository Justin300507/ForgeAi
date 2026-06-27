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
    response = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "user",
                "content": prompt
            }
        ],
        max_completion_tokens=max_tokens,
        temperature=0.2
    )

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