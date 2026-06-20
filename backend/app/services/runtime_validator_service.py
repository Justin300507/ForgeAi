from app.runtime.backend_runner import BackendRunner
from app.runtime.error_parser import parse_runtime_error

def validate_runtime(project_path):

```
try:

    runner = BackendRunner()

    result = runner.run(
        project_path
    )

    runtime_data = (
        result.model_dump()
    )

    if not runtime_data.get(
        "success",
        False
    ):

        runtime_data[
            "parsed_error"
        ] = parse_runtime_error(
            runtime_data.get(
                "stderr",
                ""
            )
        )

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
```
