from fastapi import APIRouter, Depends, HTTPException
from services.task_service import TaskService
from models.task import TaskCreate, TaskUpdate
from typing import List
import uuid

task_router = APIRouter()

@task_router.get("/")
def get_tasks() -> List[dict]:
    return TaskService.get_all_tasks()

@task_router.post("/")
def create_task(task: TaskCreate) -> dict:
    return TaskService.create_task(task)

@task_router.put("/{task_id}")
def update_task(task_id: uuid.UUID, task: TaskUpdate) -> dict:
    return TaskService.update_task(task_id, task)