from fastapi import APIRouter, Depends, HTTPException
from services.auth_service import AuthService
from models.user import UserCreate, UserLogin

auth_router = APIRouter()

@auth_router.post("/login")
async def login(user: UserLogin):
    return AuthService.login(user)

@auth_router.post("/register")
async def register(user: UserCreate):
    return AuthService.register(user)