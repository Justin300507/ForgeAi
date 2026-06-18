from fastapi import APIRouter, HTTPException
from services.book_service import BookService
from models.book import BookCreate

book_router = APIRouter()
book_service = BookService()

@book_router.get("/")
def get_books():
    return book_service.get_books()

@book_router.post("/")
def create_book(book: BookCreate):
    return book_service.create_book(book)