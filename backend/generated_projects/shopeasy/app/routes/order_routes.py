from fastapi import APIRouter
from services.order_service import OrderService

order_router = APIRouter()

@order_router.post("/")
async def create_order():
    return await OrderService.create_order()

@order_router.get("/{user_id}")
async def get_user_orders(user_id: str):
    return await OrderService.get_user_orders(user_id)