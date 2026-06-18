from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routes.auth_routes import auth_router
from routes.message_routes import message_router
from routes.group_routes import group_router

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router, prefix="/auth")
app.include_router(message_router, prefix="/messages")
app.include_router(group_router, prefix="/groups")