from fastapi import APIRouter, Depends, HTTPException
from services.expense_service import get_user_expenses, create_expense
from models.expense import ExpenseCreate
from typing import List

expense_router = APIRouter()

@expense_router.get("/")
async def get_expenses(user_id: str) -> List[dict]:
    return await get_user_expenses(user_id)

@expense_router.post("/")
async def add_expense(expense_data: ExpenseCreate):
    return await create_expense(expense_data)