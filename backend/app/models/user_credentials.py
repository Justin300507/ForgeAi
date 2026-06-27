from sqlalchemy import Column, String, Integer, ForeignKey
from app.database import Base


class UserCredentials(Base):
    __tablename__ = "user_credentials"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False, index=True)
    github_token = Column(String(512), nullable=True)
    render_api_key = Column(String(512), nullable=True)
    render_owner_id = Column(String(256), nullable=True)
    cloudflare_api_token = Column(String(512), nullable=True)
    cloudflare_account_id = Column(String(256), nullable=True)
