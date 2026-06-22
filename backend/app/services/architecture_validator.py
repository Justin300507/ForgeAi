import json
import os


def load_metadata(project_path):

    metadata_path = os.path.join(
        project_path,
        "metadata.json"
    )

    if not os.path.exists(metadata_path):
        return {}

    try:

        with open(
            metadata_path,
            "r",
            encoding="utf-8"
        ) as f:

            return json.load(f)

    except Exception:
        return {}


def validate_architecture(
    project_path,
    metadata,
    errors
):

    plan = metadata.get("plan", {})

    tech_stack = [
        tech.lower()
        for tech in plan.get("tech_stack", [])
    ]

    detected = set()

    for root, _, files in os.walk(project_path):

        for file in files:

            path = os.path.join(root, file)

            try:

                with open(
                    path,
                    "r",
                    encoding="utf-8",
                    errors="ignore"
                ) as f:

                    content = f.read().lower()

            except Exception:
                continue

            if "from fastapi" in content:
                detected.add("fastapi")

            if "express(" in content:
                detected.add("node.js")

            if "mongoose" in content:
                detected.add("mongodb")

            if "sqlite" in content:
                detected.add("sqlite")

            if "react" in content:
                detected.add("react")

    if "node.js" in tech_stack and "fastapi" in detected:

        errors.append(
            "Architecture violation: "
            "Planner requested Node.js "
            "but FastAPI detected"
        )

    if "mongodb" in tech_stack and "sqlite" in detected:

        errors.append(
            "Architecture violation: "
            "Planner requested MongoDB "
            "but SQLite detected"
        )

    if "react" in tech_stack and "react" not in detected:

        errors.append(
            "Architecture violation: "
            "React frontend not detected"
        )

    if "fastapi" in tech_stack and "fastapi" not in detected:

        errors.append(
            "Architecture violation: "
            "FastAPI requested but not detected — "
            "backend generation may have failed"
        )

    if "sqlite" in tech_stack and "sqlite" not in detected:

        errors.append(
            "Architecture violation: "
            "SQLite requested but not detected"
        )

    if "mongodb" in tech_stack and "mongodb" not in detected:

        errors.append(
            "Architecture violation: "
            "MongoDB requested but not detected"
        )