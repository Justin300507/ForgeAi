DATABASE_PY_TEMPLATE = '''import importlib
import os
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./app.db")

# Render/Heroku provide postgres:// but SQLAlchemy 1.4+ requires postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

if DATABASE_URL.startswith("sqlite"):
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
else:
    engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=300)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def _add_missing_columns() -> None:
    """ALTER TABLE ADD COLUMN for any mapped columns missing from existing tables."""
    try:
        from sqlalchemy import inspect as _inspect, text as _text
        insp = _inspect(engine)
        existing_tables = set(insp.get_table_names())
        with engine.begin() as conn:
            for table in Base.metadata.sorted_tables:
                if table.name not in existing_tables:
                    continue
                try:
                    existing_cols = {c["name"] for c in insp.get_columns(table.name)}
                except Exception:
                    continue
                for col in table.columns:
                    if col.name in existing_cols:
                        continue
                    try:
                        type_str = col.type.compile(dialect=engine.dialect)
                    except Exception:
                        type_str = "TEXT"
                    try:
                        conn.execute(_text(
                            f"ALTER TABLE {table.name} ADD COLUMN {col.name} {type_str}"
                        ))
                    except Exception:
                        pass
    except Exception:
        pass


def create_tables():
    """Import every model in app/models/ then create all tables."""
    models_dir = os.path.join(os.path.dirname(__file__), "models")
    if os.path.isdir(models_dir):
        for fname in sorted(os.listdir(models_dir)):
            if fname.endswith(".py") and fname not in ("__init__.py",):
                mod_name = fname[:-3]
                try:
                    importlib.import_module(f"app.models.{mod_name}")
                except Exception:
                    pass
    Base.metadata.create_all(bind=engine)
    if DATABASE_URL.startswith("sqlite"):
        _add_missing_columns()


def get_db():
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
'''

AUTH_UTILS_TEMPLATE = '''import os
from datetime import datetime, timedelta
from typing import Optional
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from app.database import get_db

SECRET_KEY = os.environ.get("SECRET_KEY", "please-set-SECRET_KEY-env-var-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: int = payload.get("sub")
        if user_id is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    # Import User model lazily to avoid circular imports
    try:
        from app.models.user import User
        user = db.query(User).filter(User.id == int(user_id)).first()
    except Exception:
        raise credentials_exception
    if user is None:
        raise credentials_exception
    return user
'''