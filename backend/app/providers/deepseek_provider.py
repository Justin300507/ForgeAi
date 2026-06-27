import os

from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

client = OpenAI(
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url="https://aicredits.in/v1",
    timeout=120,
)

DEFAULT_MODEL = "deepseek/deepseek-v3.2"


def generate(
    prompt: str,
    max_tokens: int = 4000,
    model: str = DEFAULT_MODEL,
):
    stream = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
        temperature=0.2,
        stream=True,
    )

    chunks = []
    prompt_tokens = 0
    completion_tokens = 0

    for chunk in stream:
        delta = chunk.choices[0].delta if chunk.choices else None
        if delta and delta.content:
            chunks.append(delta.content)
        # capture usage from the final chunk if provided
        if hasattr(chunk, "usage") and chunk.usage:
            prompt_tokens = chunk.usage.prompt_tokens or 0
            completion_tokens = chunk.usage.completion_tokens or 0

    result = "".join(chunks)

    if prompt_tokens or completion_tokens:
        print(
            f"[DEEPSEEK] "
            f"Prompt={prompt_tokens} "
            f"Completion={completion_tokens} "
            f"Total={prompt_tokens + completion_tokens}"
        )
    else:
        print(f"[DEEPSEEK] Streamed {len(result)} chars")

    return result
