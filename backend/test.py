from openai import OpenAI
from dotenv import load_dotenv
import os

load_dotenv()

client = OpenAI(
    api_key=os.getenv("CEREBRAS_API_KEY"),
    base_url="https://api.cerebras.ai/v1"
)

response = client.chat.completions.create(
    model="gpt-oss-120b",
    messages=[
        {
            "role": "user",
            "content": "Write a hello world FastAPI app"
        }
    ]
)

print(response.choices[0].message.content)

if hasattr(response, "usage"):
    print("\nUsage:")
    print(response.usage)