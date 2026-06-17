from fastapi import APIRouter, HTTPException
from uuid import UUID
from typing import Optional
from datetime import datetime
from pydantic import BaseModel

router = APIRouter()

class Task(BaseModel):
    id: UUID
    title: str
    description: Optional[str] = None
    dueDate: Optional[datetime] = None
    isCompleted: bool
    categoryId: Optional[UUID] = None

@router.post("/")
async def create_task(task: Task):
    return task

@router.get("/{task_id}")
async def get_task(task_id: UUID):
    return {"id": task_id}

@router.put("/{task_id}")
async def update_task(task_id: UUID, task: Task):
    return task