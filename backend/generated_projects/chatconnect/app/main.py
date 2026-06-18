from fastapi import FastAPI
from routes.auth_routes import auth_router
from routes.message_routes import message_router
from routes.group_routes import group_router

app = FastAPI()

app.include_router(auth_router, prefix="/auth")
app.include_router(message_router, prefix="/messages")
app.include_router(group_router, prefix="/groups")