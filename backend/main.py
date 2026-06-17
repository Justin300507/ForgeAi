from fastapi import FastAPI
from pydantic import BaseModel

from app.services.architect_service import generate_architecture
from app.services.backend_service import generate_backend
from app.services.frontend_service import generate_frontend
from app.services.project_service import generate_project

app = FastAPI()


class ProjectIdea(BaseModel):
    idea: str
    provider: str = "auto"


class ArchitectureRequest(BaseModel):
    project_plan: dict


class BackendRequest(BaseModel):
    architecture: dict


class FrontendRequest(BaseModel):
    architecture: dict


@app.post("/generate")
def generate(project: ProjectIdea):
    return generate_project(
        project.idea,
        project.provider
    )


@app.post("/architect")
def architect(request: ArchitectureRequest):
    return generate_architecture(request.project_plan)


@app.post("/backend")
def backend(request: BackendRequest):
    return generate_backend(request.architecture)


@app.post("/frontend")
def frontend(request: FrontendRequest):
    return generate_frontend(request.architecture)


@app.post("/project")
def project(project: ProjectIdea):
    return generate_project(
        project.idea,
        project.provider
    )