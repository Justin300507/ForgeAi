from sqlalchemy import Column, String, Integer, ForeignKey, DateTime, Text
from sqlalchemy.sql import func
from app.database import Base


class GenerationJob(Base):
    __tablename__ = "generation_jobs"

    id = Column(String(36), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    idea = Column(Text, nullable=False)
    provider = Column(String(32), default="auto")
    deploy_to = Column(String(32), default="none")
    frontend_target = Column(String(16), default="web")
    status = Column(String(16), default="pending", index=True)
    project_name = Column(String(256), nullable=True)
    forge_score = Column(Integer, nullable=True)
    backend_url = Column(String(512), nullable=True)
    frontend_url = Column(String(512), nullable=True)
    github_url = Column(String(512), nullable=True)
    zip_path = Column(String(512), nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)
