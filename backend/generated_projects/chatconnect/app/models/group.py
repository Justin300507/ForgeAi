from pydantic import BaseModel

class GroupCreate(BaseModel):
    name: str

class Group(GroupCreate):
    id: str
    createdAt: str

    class Config:
        orm_mode = True