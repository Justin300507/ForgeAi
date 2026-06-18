import re


def parse_runtime_error(stderr):

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

    if "SyntaxError" in stderr:

        return {
            "type": "SyntaxError"
        }

    return {
        "type": "Unknown"
    }