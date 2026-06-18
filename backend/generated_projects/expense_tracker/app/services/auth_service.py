from models.user import UserCreate
from typing import Dict

async def register_user(user_data: UserCreate) -> Dict[str, str]:
    # Implementation would hash password and save to DB
    return {"message": "User registered successfully"}

async def authenticate_user(email: str, password: str) -> Dict[str, str]:
    # Implementation would verify credentials
    return {"access_token": "sample_token", "token_type": "bearer"}