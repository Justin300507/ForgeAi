from fastapi import APIRouter, Depends, HTTPException
from services.user_service import UserService
from models.user import UserCreate, UserLogin

user_router = APIRouter()

@user_router.post("/register")
async def register(user: UserCreate):
    return await UserService.register_user(user)

@user_router.post("/login")
async def login(user: UserLogin):
    return await UserService.authenticate_user(user)