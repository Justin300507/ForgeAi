import os
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

_DEFAULT_DB = "sqlite:////data/forgeai.db" if os.path.isdir("/data") else "sqlite:///./forgeai.db"
DATABASE_URL = os.environ.get("DATABASE_URL") or _DEFAULT_DB  # `or` handles empty string from Railway
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

_engine_kwargs = (
    {"connect_args": {"check_same_thread": False}}
    if DATABASE_URL.startswith("sqlite")
    # pool_pre_ping tests each connection before use (recovers from a
    # provider closing an idle connection); pool_recycle=300 recycles
    # connections older than 5 min so none survive long enough to hit
    # Neon's own idle-connection timeout. Missing here caused a live
    # incident (2026-07-16): a job's final status write/read after a
    # 600s+ generation hit "psycopg2.OperationalError: SSL connection
    # has been closed unexpectedly" -- the exact failure class this
    # app's OWN database_patcher.py already fixes in every GENERATED
    # project's database.py, just never applied to ForgeAI's own.
    else {"pool_pre_ping": True, "pool_recycle": 300}
)
engine = create_engine(DATABASE_URL, **_engine_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
