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


def try_repair_truncated(text: str) -> dict | None:
    """
    Salvage complete file objects from a truncated LLM JSON response.

    When Cerebras hits its token limit mid-stream, the response is cut
    off inside the last file object.  Every file object that was fully
    written before the cut-off is still valid JSON, so we can extract
    those and let the missing-file agent regenerate the rest.

    Returns {"files": [...]} with only the complete objects, or None if
    we cannot find at least one complete file.
    """
    m = re.search(r'"files"\s*:\s*\[', text)
    if not m:
        return None

    pos = m.end()          # character after '['
    complete_files: list = []

    while pos < len(text):
        # skip whitespace / commas between array elements
        while pos < len(text) and text[pos] in ' \t\n\r,':
            pos += 1

        if pos >= len(text) or text[pos] == ']':
            break

        if text[pos] != '{':
            break

        # walk forward tracking JSON depth — skip the content of strings
        depth = 0
        in_string = False
        escape_next = False
        obj_end = -1

        for i in range(pos, len(text)):
            ch = text[i]
            if escape_next:
                escape_next = False
                continue
            if ch == '\\' and in_string:
                escape_next = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    obj_end = i
                    break

        if obj_end == -1:
            break  # incomplete — truncation happened here

        obj_text = text[pos:obj_end + 1]
        try:
            obj = json.loads(obj_text, strict=False)
            if isinstance(obj, dict) and 'path' in obj and 'content' in obj:
                complete_files.append(obj)
        except Exception:
            pass

        pos = obj_end + 1

    if complete_files:
        print(
            f"[TRUNCATION REPAIR] Salvaged {len(complete_files)} complete file(s) "
            f"from truncated response ({len(text)} chars)"
        )
        return {"files": complete_files}
    return None


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
        # Last resort: try to salvage complete file objects from truncated output
        repaired = try_repair_truncated(text)
        if repaired:
            return repaired
        return json.loads(json_text, strict=False)