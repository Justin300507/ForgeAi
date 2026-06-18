from fastapi import APIRouter, HTTPException
from services.member_service import MemberService
from models.member import MemberCreate

member_router = APIRouter()
member_service = MemberService()

@member_router.get("/")
def get_members():
    return member_service.get_members()