from pydantic import BaseModel
from typing import Optional
from uuid import UUID
from datetime import date

class TransactionBase(BaseModel):
    bookId: UUID
    memberId: UUID
    borrowDate: date

class TransactionCreate(TransactionBase):
    pass

class Transaction(TransactionBase):
    id: UUID

    class Config:
        orm_mode = True