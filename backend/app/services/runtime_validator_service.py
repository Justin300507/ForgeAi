from app.runtime.backend_runner import BackendRunner
from app.runtime.error_parser import parse_runtime_error

def validate_runtime(project_path):

    runner = BackendRunner()

    result = runner.run(project_path)

    runtime_data = result.model_dump()

    if not runtime_data["success"]:

        runtime_data["parsed_error"] = (
            parse_runtime_error(
                runtime_data["stderr"]
            )
        )

    return runtime_data