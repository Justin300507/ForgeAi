from fastapi import APIRouter, Depends, HTTPException
from services.message_service import MessageService
from models.message import MessageCreate

message_router = APIRouter()

@message_router.post("/send")
async def send_message(message: MessageCreate):
    return MessageService.send_message(message)

@message_router.get("/history")
async def get_message_history(receiver_id: str = None, group_id: str = None):
    return MessageService.get_message_history(receiver_id, group_id)