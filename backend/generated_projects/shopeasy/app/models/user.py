from pydantic import BaseModel, EmailStr

class UserCreate(BaseModel):
    name: str
    email: EmailStr
    password: str
    address: str | None = None

class UserLogin(BaseModel):
    email: EmailStr
    password: str