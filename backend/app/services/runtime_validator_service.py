from app.runtime.backend_runner import BackendRunner
from app.runtime.error_parser import parse_runtime_error


def validate_runtime(project_path, architecture=None, port: int = 8001):

    try:

        runner = BackendRunner()

        result = runner.run(
            project_path,
            architecture=architecture,
            port=port,
        )

        runtime_data = (
            result.model_dump()
        )

        if not runtime_data.get("success", False):
            parsed_error = parse_runtime_error(runtime_data.get("stderr", ""))

            # When stderr is clean but CRUD journey failed, give the fixer specific context
            if (
                parsed_error.get("type") == "Unknown"
                and runtime_data.get("crud_passed") is False
            ):
                journey_data = runtime_data.get("journey") or {}
                failed_steps = [
                    (s.get("name", "?"), s.get("detail", ""))
                    for s in journey_data.get("steps", [])
                    if not s.get("passed", True)
                ]
                if failed_steps:
                    parsed_error = {
                        "type": "JourneyCRUDFailure",
                        "failed_steps": failed_steps,
                        "error_file": None,
                        "hint": (
                            "The server started without errors but CRUD operations failed. "
                            f"Failed steps: {failed_steps}. "
                            "Fix the route handlers for the detected CRUD resource. "
                            "If 'Create entity: 422' — the request schema rejects the test payload; "
                            "make optional fields have defaults or remove strict validation. "
                            "If 'Edit entity: no entity_id captured' — the create step failed first. "
                            "Do NOT modify pydantic or fastapi source files."
                        ),
                    }

            runtime_data["parsed_error"] = parsed_error

        return runtime_data

    except Exception as e:

        return {
            "success": False,
            "exit_code": -1,
            "stdout": "",
            "stderr": str(e),
            "parsed_error": {
                "type": "runtime_validator_failure",
                "message": str(e)
            }
        }