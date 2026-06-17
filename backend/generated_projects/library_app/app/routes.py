from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm

router = APIRouter()

@router.post("/api/auth/login")
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    return {"access_token": "sample_token", "token_type": "bearer"}

@router.get("/api/books")
async def get_books():
    return [{"title": "Sample Book", "author": "Author Name"}]

@router.post("/api/transactions/checkout")
async def checkout_book():
    return {"message": "Checkout successful"}