from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPBearer

from services.group_service import GroupService
from models.group import GroupCreate

group_router = APIRouter()
security = HTTPBearer()

@group_router.post("/create")
async def create_group(group: GroupCreate, token: str = Depends(security)):
    return await GroupService.create_group(group, token)