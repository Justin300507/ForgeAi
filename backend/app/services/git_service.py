import subprocess


def initialize_git(
    project_path
):

    try:

        subprocess.run(
            ["git", "init"],
            cwd=project_path,
            capture_output=True
        )

    except Exception as e:

        print(
            f"Git Init Failed: {e}"
        )