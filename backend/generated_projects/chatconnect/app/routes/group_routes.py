from fastapi import APIRouter, Depends, HTTPException
from services.group_service import GroupService
from models.group import GroupCreate

group_router = APIRouter()

@group_router.post("/create")
async def create_group(group: GroupCreate):
    return GroupService.create_group(group)