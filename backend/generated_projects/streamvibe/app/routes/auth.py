from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm

router = APIRouter(prefix="/api/v1")

@router.post("/login")
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    # Authentication logic here
    return {"access_token": "sample_token", "token_type": "bearer"}