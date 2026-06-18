from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from services.auth_service import AuthService
from models.user import UserCreate

auth_router = APIRouter()

@auth_router.post("/register")
def register(user: UserCreate):
    return AuthService.register_user(user)

@auth_router.post("/login")
def login(form_data: OAuth2PasswordRequestForm = Depends()):
    return AuthService.authenticate_user(form_data.username, form_data.password)