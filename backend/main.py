from fastapi import FastAPI
from pydantic import BaseModel
from google import genai

app = FastAPI()
client = genai.Client(
    api_key="AQ.Ab8RN6IIS4CGBRYgzHs_UVN4cFxDUE-zrm1xi9JrudCx5meFbw"
)

class ProjectIdea(BaseModel):
    idea: str

@app.get("/")
def home():
    return {"message": "ForgeAI Running"}

@app.post("/generate")
def generate(project: ProjectIdea):

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=f"""
        Create a software project plan.

        Idea:
        {project.idea}

        Include:
        - Project Name
        - Features
        - Tech Stack
        - Roadmap
        """
    )

    return {
        "idea": project.idea,
        "plan": response.text}