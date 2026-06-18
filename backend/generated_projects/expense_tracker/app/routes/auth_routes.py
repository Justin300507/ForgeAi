from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from services.auth_service import register_user, authenticate_user
from models.user import UserCreate

auth_router = APIRouter()

@auth_router.post("/register")
async def register(user_data: UserCreate):
    return await register_user(user_data)

@auth_router.post("/login")
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    return await authenticate_user(form_data.username, form_data.password)