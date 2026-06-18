from models.task import TaskCreate, TaskUpdate, TaskInDB
from typing import List, Dict
import uuid
from datetime import datetime

class TaskService:
    fake_db: List[Dict] = []

    @staticmethod
    def get_all_tasks() -> List[Dict]:
        return TaskService.fake_db

    @staticmethod
    def create_task(task: TaskCreate) -> Dict:
        new_task = {
            "id": str(uuid.uuid4()),
            "title": task.title,
            "description": task.description,
            "dueDate": task.dueDate.isoformat() if task.dueDate else None,
            "priority": task.priority,
            "status": task.status,
            "userId": "fake-user-id"
        }
        TaskService.fake_db.append(new_task)
        return new_task

    @staticmethod
    def update_task(task_id: uuid.UUID, task: TaskUpdate) -> Dict:
        for t in TaskService.fake_db:
            if t["id"] == str(task_id):
                if task.title is not None:
                    t["title"] = task.title
                if task.description is not None:
                    t["description"] = task.description
                if task.dueDate is not None:
                    t["dueDate"] = task.dueDate.isoformat()
                if task.priority is not None:
                    t["priority"] = task.priority
                if task.status is not None:
                    t["status"] = task.status
                return t
        raise ValueError("Task not found")