from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm

from services.auth_service import AuthService
from models.user import UserCreate, UserLogin

auth_router = APIRouter()

@auth_router.post("/register")
async def register(user: UserCreate):
    return await AuthService.register_user(user)

@auth_router.post("/login")
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    return await AuthService.login_user(form_data.username, form_data.password)