from sqlalchemy import Column, String, Integer, Float, ForeignKey, DateTime, Text
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
    # Additive V15 supervisor state.  These fields deliberately contain only
    # bounded identifiers/timestamps: never prompts, provider responses, JWTs,
    # or raw exception messages.
    progress_stage = Column(String(64), nullable=True)
    progress_updated_at = Column(DateTime(timezone=True), nullable=True)
    effective_provider = Column(String(32), nullable=True)
    execution_token = Column(String(64), nullable=True, index=True)
    lease_expires_at = Column(DateTime(timezone=True), nullable=True)
    deadline_at = Column(DateTime(timezone=True), nullable=True)
    # Persisted accounting lets the jobs dashboard report the actual
    # generation estimate for this run without inferring it from a shared log.
    total_tokens = Column(Integer, nullable=True)
    estimated_cost_usd = Column(Float, nullable=True)
    cache_hits = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)
