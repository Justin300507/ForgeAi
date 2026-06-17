from fastapi import APIRouter, Depends, HTTPException
from models.user import UserCreate
from services.user_service import create_user

router = APIRouter()

@router.post("/api/users/register")
async def register_user(user: UserCreate):
    return await create_user(user)

@router.get("/api/workouts")
async def get_workouts():
    return {"message": "Workouts endpoint"}

@router.post("/api/nutrition")
async def add_nutrition():
    return {"message": "Nutrition endpoint"}