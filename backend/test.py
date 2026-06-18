from groq import Groq

client = Groq(
    api_key="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"
)

response = client.chat.completions.create(
    model="llama-3.3-70b-versatile",
    messages=[
        {
            "role": "user",
            "content": "Say hello"
        }
    ]
)

print(
    response.choices[0].message.content
)