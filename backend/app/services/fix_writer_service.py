import os


def write_fix(project_path, fix):

    full_path = os.path.join(
        project_path,
        fix["path"]
    )

    os.makedirs(
        os.path.dirname(full_path),
        exist_ok=True
    )

    with open(
        full_path,
        "w",
        encoding="utf-8"
    ) as f:

        f.write(
            fix["content"]
        )