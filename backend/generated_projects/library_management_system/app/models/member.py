from pydantic import BaseModel
from typing import Optional
from uuid import UUID

class MemberBase(BaseModel):
    name: str
    email: str

class MemberCreate(MemberBase):
    pass

class Member(MemberBase):
    id: UUID

    class Config:
        orm_mode = True