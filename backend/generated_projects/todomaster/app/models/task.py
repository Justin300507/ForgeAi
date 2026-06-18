from pydantic import BaseModel
from typing import Optional
from datetime import datetime
import uuid

class TaskBase(BaseModel):
    title: str
    description: Optional[str] = None
    dueDate: Optional[datetime] = None
    priority: int
    status: str

class TaskCreate(TaskBase):
    pass

class TaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    dueDate: Optional[datetime] = None
    priority: Optional[int] = None
    status: Optional[str] = None

class TaskInDB(TaskBase):
    id: uuid.UUID
    userId: uuid.UUID

    class Config:
        orm_mode = True