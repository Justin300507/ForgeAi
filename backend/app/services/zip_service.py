import os
import zipfile

# Directories and file patterns that should never end up in the export zip
_SKIP_DIRS = {"node_modules", "dist", ".git", "__pycache__", ".venv", "venv"}
_SKIP_EXTS = {".db", ".db-shm", ".db-wal", ".pyc", ".pyo"}


def create_zip(project_path):

    zip_path = f"{project_path}.zip"

    with zipfile.ZipFile(
        zip_path,
        "w",
        zipfile.ZIP_DEFLATED
    ) as zipf:

        for root, dirs, files in os.walk(project_path):

            # Prune skip-dirs in-place so os.walk doesn't descend into them
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]

            for file in files:
                if any(file.endswith(ext) for ext in _SKIP_EXTS):
                    continue

                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, project_path)

                try:
                    zipf.write(file_path, arcname)
                except (PermissionError, OSError) as e:
                    print(f"Zip: skipping locked file {arcname} ({e})")

    return zip_path