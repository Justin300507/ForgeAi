from pydantic import BaseModel
from datetime import datetime

class OrderCreate(BaseModel):
    user_id: str
    total: float
    status: str

class Order(BaseModel):
    id: str
    user_id: str
    total: float
    status: str
    date: datetime