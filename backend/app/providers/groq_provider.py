import os

from groq import Groq


client = Groq(
    api_key=os.getenv("GROQ_API_KEY")
)


def generate(
    prompt,
    max_tokens=2000
):

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {
                "role": "user",
                "content": prompt
            }
        ],
        max_tokens=max_tokens
    )

    content = response.choices[0].message.content

    if not content:
        raise Exception("Groq returned empty content")

    return content