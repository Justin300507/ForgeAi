from openai import OpenAI
import os
from dotenv import load_dotenv

load_dotenv()

client = OpenAI(
    api_key=os.getenv(
        "OPENROUTER_API_KEY"
    ),
    base_url="https://openrouter.ai/api/v1"
)


def generate(
    prompt,
    max_tokens=4000
):

    print(
        f"OpenRouter Max Tokens: {max_tokens}"
    )

    response = client.chat.completions.create(
        model="deepseek/deepseek-chat-v3-0324",
        max_tokens=max_tokens,
        messages=[
            {
                "role": "system",
                "content": """
You are a JSON API.

Return ONLY valid JSON.
Do not return markdown.
Do not return explanations.
Do not return headings.
Do not return notes.
Do not return text before or after JSON.
"""
            },
            {
                "role": "user",
                "content": prompt
            }
        ]
    )

    print(
        "\n=== OPENROUTER RAW RESPONSE ==="
    )

    print(response)

    if response is None:

        raise Exception(
            "OpenRouter returned None response"
        )

    if not hasattr(
        response,
        "choices"
    ):

        raise Exception(
            f"OpenRouter response missing choices: {response}"
        )

    if not response.choices:

        raise Exception(
            f"OpenRouter returned empty choices: {response}"
        )

    if (
        response.choices[0].message
        is None
    ):

        raise Exception(
            f"OpenRouter returned empty message: {response}"
        )

    if (
        response.choices[0]
        .message
        .content
        is None
    ):

        raise Exception(
            f"OpenRouter returned empty content: {response}"
        )

    return (
        response
        .choices[0]
        .message
        .content
    )