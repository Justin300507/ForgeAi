from pydantic import BaseModel
from typing import Optional
from uuid import UUID

class BookBase(BaseModel):
    title: str
    author: str
    ISBN: str

class BookCreate(BookBase):
    pass

class Book(BookBase):
    id: UUID

    class Config:
        orm_mode = True