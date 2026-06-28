from pydantic import BaseModel
from typing import Optional


class RuntimeResult(BaseModel):
    success: bool
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
