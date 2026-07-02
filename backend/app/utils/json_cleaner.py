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
    r'("[a-zA-Z_][a-zA-Z0-9_]*"\s*:\s*)"""(.*?)"""',
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


def _escape_inner_quotes(text: str) -> str:
    """
    Walk the JSON character-by-character and escape any unescaped double quotes
    that appear *inside* a string value (not at string boundaries).

    Closing-quote heuristics (after a '"' while in_string):
      ':' or '}' or end  → always closing
      ',' followed by \\n  → JSON field separator, so closing
      ',' followed by space/text → English prose comma, so inner quote (escape)
      ']' followed by \\n or '}' → JSON array end, so closing
      ']' followed by space/text → Python indexer e.g. obj["key"], so inner (escape)
      anything else → inner quote, escape it
    """
    out = []
    i = 0
    n = len(text)
    in_string = False
    escaped = False

    while i < n:
        ch = text[i]

        if escaped:
            out.append(ch)
            escaped = False
            i += 1
            continue

        if ch == '\\':
            out.append(ch)
            if in_string:
                escaped = True
            i += 1
            continue

        if ch == '"':
            if not in_string:
                in_string = True
                out.append(ch)
                i += 1
                continue

            # Inside a string — peek ahead (skip only spaces, not newlines)
            j = i + 1
            while j < n and text[j] == ' ':
                j += 1
            next_ch = text[j] if j < n else ''

            is_closing = False
            if next_ch in (':', '}', ''):
                is_closing = True
            elif next_ch == ',':
                # Closing only if comma is immediately followed by newline (JSON separator)
                k = j + 1
                while k < n and text[k] == ' ':
                    k += 1
                after_comma = text[k] if k < n else ''
                is_closing = after_comma in ('\n', '\r', '')
            elif next_ch == ']':
                # Closing only if ] is followed by newline or } (JSON array end)
                k = j + 1
                while k < n and text[k] == ' ':
                    k += 1
                after_bracket = text[k] if k < n else ''
                is_closing = after_bracket in ('\n', '\r', ',', '}', '')
            elif next_ch in ('\n', '\r'):
                is_closing = True

            if is_closing:
                in_string = False
                out.append(ch)
            else:
                out.append('\\')
                out.append('"')
            i += 1
            continue

        out.append(ch)
        i += 1

    return ''.join(out)


def _extract_single_file_object(text: str):
    """
    Robustly extract {"path": "...", "content": "..."} when the content value
    contains unescaped quotes the LLM forgot to escape -- e.g. an embedded
    Python dict literal like `model_config = {"from_attributes": True}`.

    The generic string-token regex (and the quote/colon heuristic in
    _escape_inner_quotes) both treat `"from_attributes":` as if it were a
    real JSON key boundary and truncate/corrupt the content field. This
    exploits a known invariant instead: the fixer/missing-file/runtime-fix
    prompts always return exactly this two-key shape with "content" as the
    LAST field, so its closing quote is the last unescaped `"` before the
    object's closing brace -- no character-by-character guessing needed.
    """
    # Bail out on the multi-file {"files": [...]} shape -- this extractor
    # assumes exactly one "path"/"content" pair in the whole text and would
    # otherwise splice content across file boundaries.
    if '"files"' in text:
        return None

    path_m = re.search(r'"path"\s*:\s*"((?:[^"\\]|\\.)*)"', text)
    content_key_m = re.search(r'"content"\s*:\s*"', text)
    if not path_m or not content_key_m:
        return None

    content_start = content_key_m.end()
    close_brace = text.rfind("}")
    if close_brace == -1 or close_brace < content_start:
        return None

    tail = text[content_start:close_brace]
    last_quote = tail.rfind('"')
    if last_quote == -1:
        return None

    raw_content = tail[:last_quote]
    # Undo the "path" backslash-doubling _fix_path_backslashes already applied
    path_value = path_m.group(1).replace('\\\\', '\\').replace('\\"', '"')

    return {"path": path_value, "content": raw_content}


def _find_matching_close_brace(text: str, open_pos: int) -> int:
    """
    Given the index of a '{' in text, walk forward tracking string/escape
    state to find the index of its MATCHING '}' -- correctly skipping braces
    inside string literals, and stopping at the actual end of the JSON object
    rather than grabbing a LATER '}' that happens to appear in trailing
    non-JSON text the LLM appended after a complete, valid object (the
    "Extra data" class of json.JSONDecodeError). Returns -1 if unmatched
    (truncated response).
    """
    depth = 0
    in_string = False
    escape_next = False
    for i in range(open_pos, len(text)):
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
                return i
    return -1


def extract_json(text: str):

    text = text.strip()

    # Remove markdown fences
    text = re.sub(r"^```json\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^```\s*", "", text)
    text = re.sub(r"\s*```$", "", text)

    # FIX #1: Convert triple-quoted content blocks first
    text = _fix_triple_quoted_content(text)

    start = text.find("{")
    if start == -1:
        raise ValueError("No JSON found in response")

    end = _find_matching_close_brace(text, start)
    if end == -1:
        end = text.rfind("}")  # fallback: truncated/malformed, let downstream repair try
    if end == -1:
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
        # For the common single-file {"path", "content"} shape, extracting by
        # known field position sidesteps quote-boundary guessing entirely --
        # try it before the heuristic repairs below.
        single_file = _extract_single_file_object(json_text)
        if single_file is not None:
            return single_file

        # Try escaping unescaped inner quotes (common when LLM embeds code in
        # descriptions), THEN re-run the newline/tab repair on top of that --
        # a single field often has both problems (a quoted identifier AND a
        # raw newline), and fixing only one still leaves the JSON unparsable.
        try:
            quote_fixed = _escape_inner_quotes(json_text)
            quote_fixed = re.sub(r'"(?:[^"\\]|\\.)*"', _repair_string_token, quote_fixed, flags=re.DOTALL)
            return json.loads(quote_fixed, strict=False)
        except json.JSONDecodeError:
            pass
        # Last resort: try to salvage complete file objects from truncated output
        repaired = try_repair_truncated(text)
        if repaired:
            return repaired
        return json.loads(json_text, strict=False)