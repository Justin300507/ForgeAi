from pydantic import BaseModel

class UserBase(BaseModel):
    username: str
    email: str

class UserCreate(UserBase):
    password: str

class UserLogin(BaseModel):
    username: str
    password: str

class User(UserBase):
    id: str
    onlineStatus: bool

    class Config:
        orm_mode = True