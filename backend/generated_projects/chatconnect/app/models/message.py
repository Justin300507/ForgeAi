from pydantic import BaseModel
from typing import Optional

class MessageCreate(BaseModel):
    senderId: str
    receiverId: Optional[str] = None
    groupId: Optional[str] = None
    content: str

class Message(MessageCreate):
    id: str
    timestamp: str

    class Config:
        orm_mode = True