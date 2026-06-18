from fastapi import APIRouter, HTTPException
from services.transaction_service import TransactionService
from models.transaction import TransactionCreate

transaction_router = APIRouter()
transaction_service = TransactionService()

@transaction_router.post("/")
def create_transaction(transaction: TransactionCreate):
    return transaction_service.create_transaction(transaction)

@transaction_router.get("/overdue")
def get_overdue_transactions():
    return transaction_service.get_overdue_transactions()