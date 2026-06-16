from fastapi import FastAPI
from pydantic import BaseModel
from app.services.architect_service import generate_architecture

from app.services.planner_service import generate_plan

app = FastAPI()

class ProjectIdea(BaseModel):
    idea: str
class ArchitectureRequest(BaseModel):
    project_plan: dict

@app.post("/generate")
def generate(project: ProjectIdea):

    return generate_plan(project.idea)
@app.post("/architect")
def architect(request: ArchitectureRequest):
    return generate_architecture(request.project_plan)