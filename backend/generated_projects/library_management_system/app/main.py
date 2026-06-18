from fastapi import FastAPI
from routes.book_routes import book_router
from routes.member_routes import member_router
from routes.transaction_routes import transaction_router

app = FastAPI()

app.include_router(book_router, prefix="/books", tags=["books"])
app.include_router(member_router, prefix="/members", tags=["members"])
app.include_router(transaction_router, prefix="/transactions", tags=["transactions"])

@app.get("/")
def read_root():
    return {"message": "Welcome to Library Management System"}