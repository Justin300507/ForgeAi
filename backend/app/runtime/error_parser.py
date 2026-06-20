import re

def parse_runtime_error(stderr):

```
stderr = str(stderr)

if "python-multipart" in stderr:

    return {
        "type": "MissingDependency",
        "dependency": "python-multipart"
    }

if "email-validator" in stderr:

    return {
        "type": "MissingDependency",
        "dependency": "email-validator"
    }

if "No module named" in stderr:

    match = re.search(
        r"No module named ['\"](.+?)['\"]",
        stderr
    )

    return {
        "type": "ModuleNotFoundError",
        "module": (
            match.group(1)
            if match
            else None
        )
    }

if (
    "partially initialized module"
    in stderr
):

    return {
        "type": "CircularImport"
    }

if "cannot import name" in stderr:

    symbol_match = re.search(
        r"cannot import name ['\"](.+?)['\"]",
        stderr
    )

    file_match = re.search(
        r"from ['\"](.+?)['\"]",
        stderr
    )

    return {
        "type": "ImportError",
        "missing_symbol": (
            symbol_match.group(1)
            if symbol_match
            else None
        ),
        "source_module": (
            file_match.group(1)
            if file_match
            else None
        )
    }

if "FileNotFoundError" in stderr:

    return {
        "type": "FileNotFoundError"
    }

if "AttributeError" in stderr:

    return {
        "type": "AttributeError"
    }

if "ValidationError" in stderr:

    return {
        "type": "ValidationError"
    }

if "FastAPIError" in stderr:

    return {
        "type": "FastAPIError"
    }

if "Error loading ASGI app" in stderr:

    return {
        "type": "ASGIAppError"
    }

if "SyntaxError" in stderr:

    return {
        "type": "SyntaxError"
    }

return {
    "type": "Unknown",
    "raw_error": stderr[:1000]
}
```
