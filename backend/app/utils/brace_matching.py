"""
Exp053: a single string-aware "find the matching closing brace" primitive,
extracted from three independent implementations of the identical
depth-tracking algorithm (app/utils/json_cleaner.py x2 -- one named, one
inline inside try_repair_truncated -- and
app/services/validator_service.py::_extract_object_literal), found by
Experiment 051's audit and confirmed by direct body comparison, not name
matching alone.

Two of the three call sites only ever see JSON text (double-quoted strings
only, per the JSON spec); the third parses JS/JSX object literals, which
can use single quotes, double quotes, or template-literal backticks. This
is a real semantic difference, not an accident -- `quote_chars` exists so
both domains share the same tested walk instead of a forced identical
merge that would either break JSON parsing (accepting stray backticks) or
break JS parsing (rejecting single-quoted strings).
"""
from __future__ import annotations


def find_matching_brace(text: str, open_pos: int, quote_chars: str = '"') -> int:
    """
    Given the index of a '{' in text, walk forward tracking string/escape
    state to find the index of its MATCHING '}' -- correctly skipping
    braces inside string literals, and stopping at the actual end of the
    object rather than grabbing a LATER '}' that happens to appear in
    trailing text after a complete object. Returns -1 if unmatched
    (truncated input).

    quote_chars: which characters open/close a string literal in this
    text's language. Default '"' matches JSON (the only valid string
    delimiter there). Pass e.g. "'\"`" for JS/JSX source, where any of
    single quote, double quote, or backtick can delimit a string.
    """
    depth = 0
    in_string = None  # None, or the specific quote char that opened the current string
    escape_next = False
    for i in range(open_pos, len(text)):
        ch = text[i]
        if escape_next:
            escape_next = False
            continue
        if ch == "\\" and in_string is not None:
            escape_next = True
            continue
        if in_string is not None:
            if ch == in_string:
                in_string = None
            continue
        if ch in quote_chars:
            in_string = ch
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
    return -1
