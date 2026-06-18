from pydantic import BaseModel
from datetime import datetime

class MessageCreate(BaseModel):
    receiver_id: str
    content: str

class Message(BaseModel):
    id: str
    sender_id: str
    receiver_id: str
    content: str
    timestamp: datetime
    is_read: bool

    class Config:
        orm_mode = True