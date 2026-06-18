from pydantic import BaseModel, EmailStr

class UserBase(BaseModel):
    username: str
    email: EmailStr

class UserCreate(UserBase):
    password: str

class UserLogin(BaseModel):
    username: str
    password: str

class User(UserBase):
    id: str
    profile_picture: str | None = None
    status: str | None = None

    class Config:
        orm_mode = True