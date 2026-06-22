from app.services.gemini_service import client


def generate(prompt):

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt
    )

    if not response or not response.text:
        raise Exception("Gemini returned empty or blocked response")

    return response.text