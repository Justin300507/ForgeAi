from fastapi import APIRouter
from services.product_service import ProductService

product_router = APIRouter()

@product_router.get("/")
async def get_products():
    return await ProductService.get_all_products()