import os
import zipfile


def create_zip(project_path):

    zip_path = f"{project_path}.zip"

    with zipfile.ZipFile(
        zip_path,
        "w",
        zipfile.ZIP_DEFLATED
    ) as zipf:

        for root, dirs, files in os.walk(project_path):

            for file in files:

                file_path = os.path.join(root, file)

                arcname = os.path.relpath(
                    file_path,
                    project_path
                )

                zipf.write(
                    file_path,
                    arcname
                )

    return zip_path