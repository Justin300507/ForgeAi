from fastapi import APIRouter
from app.services.architect_service import generate_architecture

router = APIRouter()

@router.post("/architect")
def architect(plan: dict):

    result = generate_architecture(plan)

    return result