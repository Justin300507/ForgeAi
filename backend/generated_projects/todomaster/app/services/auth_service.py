from models.user import UserCreate, UserInDB
from typing import Dict
import uuid
from datetime import datetime

class AuthService:
    @staticmethod
    def register_user(user: UserCreate) -> Dict:
        # In a real app, hash password and save to DB
        db_user = {
            "id": str(uuid.uuid4()),
            "username": user.username,
            "email": user.email,
            "createdAt": datetime.now().isoformat()
        }
        return db_user

    @staticmethod
    def authenticate_user(username: str, password: str) -> Dict:
        # In a real app, verify password against hashed password in DB
        return {"access_token": "fake-jwt-token", "token_type": "bearer"}