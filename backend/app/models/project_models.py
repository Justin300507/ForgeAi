from pydantic import BaseModel


class DatabaseEntity(BaseModel):
    name: str
    fields: list[str]


class ApiModule(BaseModel):
    name: str
    endpoints: list[str]


class RoadmapPhase(BaseModel):
    phase: str
    milestones: list[str]


class ProjectPlan(BaseModel):
    project_name: str
    description: str
    target_users: list[str]
    core_features: list[str]
    future_features: list[str]
    tech_stack: list[str]

    database_entities: list[DatabaseEntity]
    api_modules: list[ApiModule]

    roadmap: list[RoadmapPhase]