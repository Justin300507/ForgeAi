from pydantic import BaseModel
from datetime import date
from typing import Optional

class ExpenseBase(BaseModel):
    user_id: str
    amount: float
    category: str
    date: date

class ExpenseCreate(ExpenseBase):
    description: Optional[str] = None