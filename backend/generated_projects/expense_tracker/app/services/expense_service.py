from models.expense import ExpenseCreate
from typing import List, Dict

async def get_user_expenses(user_id: str) -> List[Dict]:
    # Implementation would query DB
    return [{"id": "1", "amount": 10.5, "category": "food"}]

async def create_expense(expense_data: ExpenseCreate) -> Dict[str, str]:
    # Implementation would save to DB
    return {"message": "Expense created successfully"}