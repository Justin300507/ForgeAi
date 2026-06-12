from fastapi import FastAPI

app = FastAPI()

@app.get("/")
def home():
    return {"message": "ForgeAI Running"}

@app.post("/generate")
def generate():
    return {
        "project_name": "Gym Tracker",
        "features": [
            "Authentication",
            "Workout Logging",
            "Progress Tracking"
        ],
        "tech_stack": [
            "FastAPI",
            "React",
            "PostgreSQL"
        ]
    }