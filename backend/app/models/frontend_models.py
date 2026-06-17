from pydantic import BaseModel
from typing import List


class GeneratedFile(BaseModel):
    path: str
    content: str


class FrontendPlan(BaseModel):
    files: List[GeneratedFile]