import os
from dotenv import load_dotenv
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from typing import Optional
from jose import JWTError, jwt

from app.services.user_service import get_user_by_email, verify_password
from app.models.user import User
from app.database import get_db

# Exp070: load .env explicitly -- nothing upstream of this import
# (main.py, app/database.py) called load_dotenv() before this module's
# top-level SECRET_KEY read, so a SECRET_KEY defined only in .env
# (not exported in the shell) was silently invisible here, meaning the
# insecure default below could kick in even for a developer who
# correctly set SECRET_KEY in .env and never knew it wasn't applied.
load_dotenv()

# Exp070: known-weak/placeholder values a developer might type by hand
# or copy from an example file -- rejected the same as a missing key.
# Not an exhaustive weak-secret detector (real entropy analysis is out
# of scope for a "keep it simple" fix); a minimum-length floor plus
# this blocklist catches the realistic mistakes.
_INSECURE_SECRET_KEY_VALUES = {
    "please-set-secret_key-env-var-in-production",  # the exact prior hardcoded default, lowercased
    "please-set-secret-key-env-var-in-production",  # hyphen variant, in case of a hand-retyped copy
    "changeme", "change-me", "secret", "secretkey", "secret-key",
    "your-secret-key", "your-secret-key-here", "insecure", "password",
    "test", "testing", "dev", "development", "example",
}
_MIN_SECRET_KEY_LENGTH = 32


def _validate_secret_key(value: Optional[str]) -> str:
    """
    Exp070: fail fast and loudly at import time (i.e. at process
    startup, since this module is imported near the top of main.py)
    rather than silently falling back to a hardcoded, publicly-visible
    default. ForgeAI never auto-generates a secret on a developer's
    behalf -- a silently-generated "production" secret would be just
    as dangerous as a hardcoded one if the operator never realizes it
    needs to be pinned/persisted across restarts (every restart would
    mint a new key and invalidate every existing session with no
    warning).
    """
    stripped = (value or "").strip()
    if not stripped:
        raise RuntimeError(
            "\n\n"
            "FATAL: SECRET_KEY environment variable is not set.\n\n"
            "ForgeAI refuses to start without an explicit SECRET_KEY -- "
            "a hardcoded default would let anyone forge a valid login "
            "token for any user.\n\n"
            "Fix: generate a strong random key, e.g.:\n"
            "    python -c \"import secrets; print(secrets.token_hex(32))\"\n"
            "then set it as SECRET_KEY in your .env file or as an "
            "exported environment variable before starting the server.\n"
        )
    if stripped.lower() in _INSECURE_SECRET_KEY_VALUES:
        raise RuntimeError(
            "\n\n"
            f"FATAL: SECRET_KEY is set to a known placeholder/insecure "
            f"value ({stripped!r}).\n\n"
            "Fix: generate a strong random key, e.g.:\n"
            "    python -c \"import secrets; print(secrets.token_hex(32))\"\n"
            "then set it as SECRET_KEY in your .env file or as an "
            "exported environment variable before starting the server.\n"
        )
    if len(stripped) < _MIN_SECRET_KEY_LENGTH:
        raise RuntimeError(
            "\n\n"
            f"FATAL: SECRET_KEY is too short ({len(stripped)} characters; "
            f"minimum {_MIN_SECRET_KEY_LENGTH}).\n\n"
            "Fix: generate a strong random key, e.g.:\n"
            "    python -c \"import secrets; print(secrets.token_hex(32))\"\n"
            "then set it as SECRET_KEY in your .env file or as an "
            "exported environment variable before starting the server.\n"
        )
    return stripped


SECRET_KEY = _validate_secret_key(os.environ.get("SECRET_KEY"))
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/login")

def authenticate_user(db: Session, email: str, password: str) -> Optional[User]:
    user = get_user_by_email(db, email)
    if not user:
        return None
    if not verify_password(password, user.hashed_password):
        return None
    return user

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

async def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    user = get_user_by_email(db, email)
    if user is None:
        raise credentials_exception
    return user
