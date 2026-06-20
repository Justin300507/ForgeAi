from pydantic import BaseModel, Field


class DatabaseEntity(BaseModel):
    name: str = Field(..., min_length=1)
    fields: list[str] = Field(default_factory=list)


class ApiModule(BaseModel):
    name: str = Field(..., min_length=1)
    endpoints: list[str] = Field(default_factory=list)


class RoadmapPhase(BaseModel):
    phase: str = Field(..., min_length=1)
    milestones: list[str] = Field(default_factory=list)


class ProjectPlan(BaseModel):
    project_name: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)

    target_users: list[str] = Field(default_factory=list)

    core_features: list[str] = Field(default_factory=list)

    future_features: list[str] = Field(default_factory=list)

    tech_stack: list[str] = Field(default_factory=list)

    database_entities: list[DatabaseEntity] = Field(default_factory=list)

    api_modules: list[ApiModule] = Field(default_factory=list)

    pages: list[str] = Field(default_factory=list)

    backend_modules: list[str] = Field(default_factory=list)

    roadmap: list[RoadmapPhase] = Field(default_factory=list)