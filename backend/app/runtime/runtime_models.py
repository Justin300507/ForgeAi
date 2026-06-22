from pydantic import BaseModel
from typing import List


class RuntimeResult(BaseModel):
    success: bool
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    startup_time: float = 0.0
    behavioral_issues: list = []