from pydantic import BaseModel
from typing import List


class RoadmapPhase(BaseModel):
    phase: str
    milestones: List[str]


class ProjectPlan(BaseModel):
    project_name: str
    description: str
    target_users: List[str]
    core_features: List[str]
    future_features: List[str]
    tech_stack: List[str]
    database_entities: List[str]
    api_modules: List[str]
    roadmap: List[RoadmapPhase]