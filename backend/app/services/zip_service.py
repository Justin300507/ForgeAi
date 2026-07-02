import os
import zipfile

# Directories and file patterns that should never end up in the export zip
_SKIP_DIRS = {"node_modules", "dist", ".git", "__pycache__", ".venv", "venv"}
_SKIP_EXTS = {".db", ".db-shm", ".db-wal", ".pyc", ".pyo"}


def write_debug_report(project_path, validation=None, runtime_result=None):
    """Write validation/runtime failure details into the project directory so
    they ship inside the export zip — a failed run can be inspected from the
    downloaded zip alone, without re-running the pipeline or digging through
    server logs."""
    lines = ["# ForgeAI Build Report", ""]

    if validation is not None:
        lines.append(f"## Validation: {'PASSED' if validation.get('passed') else 'FAILED'}")
        for err in validation.get("errors", []):
            lines.append(f"- {err}")
        lines.append("")

    if runtime_result is not None:
        lines.append(f"## Runtime: {'PASSED' if runtime_result.get('success') else 'FAILED'}")

        if runtime_result.get("error"):
            lines.append(f"Error: {runtime_result['error']}")

        parsed = runtime_result.get("parsed_error") or {}
        if parsed:
            lines.append(f"Parsed error type: {parsed.get('type')}")
            if parsed.get("message"):
                lines.append(f"Message: {parsed['message']}")
            if parsed.get("hint"):
                lines.append(f"Hint: {parsed['hint']}")
            if parsed.get("failed_steps"):
                lines.append("Failed steps:")
                for name, detail in parsed["failed_steps"]:
                    lines.append(f"  - {name}: {detail}")

        stderr = runtime_result.get("stderr")
        if stderr:
            lines.append("")
            lines.append("## stderr (tail)")
            lines.append("```")
            lines.append(stderr[-8000:])
            lines.append("```")

        journey = runtime_result.get("journey") or {}
        steps = journey.get("steps") or []
        if steps:
            lines.append("")
            lines.append("## Journey steps")
            for s in steps:
                status = "PASS" if s.get("passed") else "FAIL"
                lines.append(f"- [{status}] {s.get('name', '?')}: {s.get('detail', '')}")

    report_path = os.path.join(project_path, "FORGEAI_BUILD_REPORT.md")
    try:
        with open(report_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
    except OSError as e:
        print(f"Zip: could not write debug report ({e})")
    return report_path


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