from google import genai
from dotenv import load_dotenv
import os

load_dotenv()

_client = None


def get_client():
    global _client
    if _client is None:
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError(
                "GEMINI_API_KEY not set — add it to .env or Railway environment variables"
            )
        _client = genai.Client(api_key=api_key)
    return _client


# Backwards-compat alias — only resolves when actually called
class _LazyClient:
    def __getattr__(self, name):
        return getattr(get_client(), name)


client = _LazyClient()
