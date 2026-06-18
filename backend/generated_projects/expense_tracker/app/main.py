from fastapi import FastAPI
from routes.auth_routes import auth_router
from routes.expense_routes import expense_router

app = FastAPI()

app.include_router(auth_router, prefix="/auth")
app.include_router(expense_router, prefix="/expenses")