from openai import OpenAI
import os
from dotenv import load_dotenv

load_dotenv()

client = OpenAI(
    api_key=os.getenv("OPENROUTER_API_KEY"),
    base_url="https://openrouter.ai/api/v1"
)
def generate(prompt):

    response = client.chat.completions.create(
        model="deepseek/deepseek-chat-v3-0324",
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

    return response.choices[0].message.content