"""Authentication endpoints: login, change password, current user."""
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.database import get_session
from app.models import User
from app.core.auth import verify_password, hash_password, create_access_token, get_current_user
from app.schemas import LoginRequest, TokenResponse, ChangePasswordRequest, UserRead

router = APIRouter(prefix="/auth", tags=["auth"])

# Brute-force lockout: after this many consecutive failed logins, the
# account is locked for LOCKOUT_MINUTES. PiTun is LAN-only with no captcha
# or IP throttling, so this per-account guard is the primary defence.
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_MINUTES = 15


def _as_utc(dt: datetime) -> datetime:
    """Treat a naive DB datetime as UTC (SQLite stores tz-naive)."""
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, session: AsyncSession = Depends(get_session)):
    now = datetime.now(timezone.utc)
    user = (await session.exec(select(User).where(User.username == body.username))).first()

    # Reject a currently-locked account before touching the password — the
    # response never reveals whether the password was right, and a locked
    # account can't have its window extended by more guesses.
    if user and user.lock_until:
        if _as_utc(user.lock_until) > now:
            retry_s = int((_as_utc(user.lock_until) - now).total_seconds()) + 1
            raise HTTPException(
                status_code=429,
                detail=f"Too many failed logins. Try again in {retry_s}s.",
                headers={"Retry-After": str(retry_s)},
            )
        # Window elapsed — clean slate before re-checking the password.
        user.failed_attempts = 0
        user.lock_until = None

    if not user or not verify_password(body.password, user.password_hash):
        # Count the miss (only for a real account — never create lockout
        # state for unknown usernames, and the 401 is identical either way
        # so existence isn't leaked).
        if user:
            user.failed_attempts = (user.failed_attempts or 0) + 1
            if user.failed_attempts >= MAX_FAILED_ATTEMPTS:
                user.lock_until = now + timedelta(minutes=LOCKOUT_MINUTES)
                user.failed_attempts = 0  # the lock is the state now
            session.add(user)
            await session.commit()
        raise HTTPException(status_code=401, detail="Invalid username or password")

    # Mint the token BEFORE any commit — `await session.commit()` expires
    # the ORM attributes, and reading `user.username` afterwards would trip
    # a lazy load on the async session (MissingGreenlet).
    token = create_access_token(user.username)

    # Success — drop any accumulated failures / lock.
    if user.failed_attempts or user.lock_until:
        user.failed_attempts = 0
        user.lock_until = None
        session.add(user)
        await session.commit()

    return TokenResponse(access_token=token, token_type="bearer")


@router.post("/change-password", status_code=204)
async def change_password(
    body: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    if not verify_password(body.current_password, current_user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    user = (await session.exec(select(User).where(User.id == current_user.id))).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.password_hash = hash_password(body.new_password)
    session.add(user)
    await session.commit()


@router.get("/me", response_model=UserRead)
async def me(current_user: User = Depends(get_current_user)):
    return current_user
