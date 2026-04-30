"""Authentication utilities: password hashing, JWT tokens, FastAPI dependency."""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
from jose import JWTError, jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import settings
from app.database import get_session
from app.models import User

logger = logging.getLogger(__name__)

_ALGORITHM = "HS256"
_TOKEN_EXPIRE_HOURS = 24

security = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


def create_access_token(username: str, expires_hours: int = _TOKEN_EXPIRE_HOURS) -> str:
    expire = datetime.now(tz=timezone.utc) + timedelta(hours=expires_hours)
    return jwt.encode(
        {"sub": username, "exp": expire},
        settings.secret_key,
        algorithm=_ALGORITHM,
    )


def decode_token(token: str) -> Optional[str]:
    """Returns username from token or None if invalid."""
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[_ALGORITHM])
        return payload.get("sub")
    except JWTError:
        return None


def verify_token(token: str) -> str:
    """Returns username or raises ValueError if token is invalid/expired."""
    username = decode_token(token)
    if username is None:
        raise ValueError("Invalid or expired token")
    return username


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    session: AsyncSession = Depends(get_session),
) -> User:
    """FastAPI dependency: extract and verify Bearer token, return User."""
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    username = decode_token(credentials.credentials)
    if username is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")

    user = (await session.exec(select(User).where(User.username == username))).first()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    return user
