import os


def write_files(project_name, files):

    project_name = (
        project_name
        .replace(" ", "_")
        .lower()
    )

    base_dir = os.path.abspath(
        os.path.join(
            "..",
            "generated_projects",
            project_name
        )
    )

    os.makedirs(
        base_dir,
        exist_ok=True
    )

    for file in files:

        path = file["path"]
        content = file["content"]

        full_path = os.path.join(
            base_dir,
            path
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

            f.write(content)

    return base_dir
