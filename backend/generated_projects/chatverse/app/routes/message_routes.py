from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPBearer

from services.message_service import MessageService
from models.message import MessageCreate

message_router = APIRouter()
security = HTTPBearer()

@message_router.post("/send")
async def send_message(message: MessageCreate, token: str = Depends(security)):
    return await MessageService.send_message(message, token)

@message_router.get("/get")
async def get_messages(token: str = Depends(security)):
    return await MessageService.get_messages(token)