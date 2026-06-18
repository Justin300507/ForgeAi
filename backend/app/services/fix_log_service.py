import json
import os


def save_fix_log(
    project_path,
    error,
    fix
):

    log_file = os.path.join(
        project_path,
        "fix_logs.json"
    )

    logs = []

    if os.path.exists(log_file):

        with open(
            log_file,
            "r",
            encoding="utf-8"
        ) as f:

            logs = json.load(f)

    logs.append({
        "error": error,
        "fix": fix
    })

    with open(
        log_file,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            logs,
            f,
            indent=2
        )