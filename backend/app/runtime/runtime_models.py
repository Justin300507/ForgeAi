from pydantic import BaseModel
from typing import Optional


class RuntimeResult(BaseModel):
    success: bool
    # True when the server started AND /health (or /docs) answered — distinct
    # from `success`, which also requires the CRUD journey and smoke tests to
    # pass. Conflating the two made every journey failure look like a server
    # that never started (scoring 0 for startup, skipping browser stages).
    healthy: bool = False
    exit_code: Optional[int] = None
    stdout: str = ""
    stderr: str = ""
    startup_time: float = 0.0
    behavioral_issues: list = []
    # Endpoint health metrics
    total_endpoints: int = 0
    timeout_count: int = 0
    error_count: int = 0
    endpoint_pass_rate: float = 1.0   # 0.0–1.0
    # CRUD journey result (run while server is still alive)
    crud_passed: Optional[bool] = None
    journey: Optional[dict] = None
