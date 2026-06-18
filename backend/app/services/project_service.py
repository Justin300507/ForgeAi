import time

from app.services.planner_service import generate_plan
from app.services.architect_service import generate_architecture
from app.services.backend_service import generate_backend
from app.services.frontend_service import generate_frontend
from app.services.file_writer_service import write_files
from app.services.zip_service import create_zip
from app.services.metadata_service import save_metadata
from app.services.validator_service import validate_project


def generate_project(
    idea,
    provider="auto"
):

    start = time.time()

    plan = generate_plan(
        idea,
        provider
    )

    architecture = generate_architecture(
        plan,
        provider
    )

    backend = generate_backend(
        architecture,
        provider
    )

    frontend = generate_frontend(
        architecture,
        provider
    )

    all_files = []

    all_files.extend(backend["files"])
    all_files.extend(frontend["files"])

    project_path = write_files(
        plan["project_name"],
        all_files
    )

    # Calculate first
    total_time = round(time.time() - start, 2)

    # Create metadata BEFORE zip
    metadata_path = save_metadata(
        project_path,
        plan,
        provider,
        total_time
    )
    validation = validate_project(
    project_path
)

    # Then zip everything (including metadata.json)
    zip_path = create_zip(project_path)

    return {
        "plan": plan,
        "architecture": architecture,
        "backend": backend,
        "frontend": frontend,
        "project_path": project_path,
        "zip_path": zip_path,
        "metadata_path": metadata_path,
        "validation": validation,
        "generation_time_seconds": total_time
    }