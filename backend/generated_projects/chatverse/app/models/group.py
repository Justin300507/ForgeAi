from pydantic import BaseModel
from datetime import datetime

class GroupCreate(BaseModel):
    name: str
    members: list[str]

class Group(BaseModel):
    id: str
    name: str
    creator_id: str
    members: list[str]
    created_at: datetime

    class Config:
        orm_mode = True