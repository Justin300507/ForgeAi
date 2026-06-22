import json
import re


def _fix_path_backslashes(match):
    """
    Inside a "path" field specifically, every backslash is treated
    as a literal separator that needs escaping for JSON.
    """

    inner = match.group(1)
    fixed = inner.replace("\\", "\\\\")
    return f'"path": "{fixed}"'


TRIPLE_QUOTE_PATTERN = re.compile(
    r'("content"\s*:\s*)"""(.*?)"""',
    re.DOTALL
)


def _fix_triple_quoted_content(text):

    def repl(match):
        prefix = match.group(1)
        inner = match.group(2)

        # json.dumps handles all escaping safely
        escaped = json.dumps(inner)

        return f"{prefix}{escaped}"

    return TRIPLE_QUOTE_PATTERN.sub(repl, text)


def _repair_string_token(match):
    """
    General repair for string values.

    Real newlines/tabs inside generated code are usually intentional,
    so preserve them as escaped JSON sequences.
    """

    token = match.group(0)

    token = (
        token.replace("\n", "\\n")
             .replace("\t", "\\t")
             .replace("\r", "\\n")
    )

    def fix_escape(m):
        ch = m.group(1)

        if ch in {'"', '\\', '/', 'n', 't'}:
            return '\\' + ch

        return '\\\\' + ch

    return re.sub(r'\\(.)', fix_escape, token)


def extract_json(text: str):

    text = text.strip()

    # Remove markdown fences
    text = re.sub(r"^```json\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^```\s*", "", text)
    text = re.sub(r"\s*```$", "", text)

    # FIX #1: Convert triple-quoted content blocks first
    text = _fix_triple_quoted_content(text)

    start = text.find("{")
    end = text.rfind("}")

    if start == -1 or end == -1:
        raise ValueError("No JSON found in response")

    json_text = text[start:end + 1]

    # Fix Windows-style paths
    sanitized = re.sub(
        r'"path"\s*:\s*"((?:[^"\\]|\\.)*)"',
        _fix_path_backslashes,
        json_text
    )

    # Repair remaining string tokens
    sanitized = re.sub(
        r'"(?:[^"\\]|\\.)*"',
        _repair_string_token,
        sanitized,
        flags=re.DOTALL
    )

    try:
        return json.loads(sanitized, strict=False)

    except json.JSONDecodeError:
        return json.loads(json_text, strict=False)