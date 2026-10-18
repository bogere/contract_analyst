"""Password hashing, signed session cookies, and authentication schemas."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, TypeAlias

import jwt
from fastapi import Cookie, Depends, HTTPException, Response
from pydantic import BaseModel, EmailStr, Field, field_validator
from pwdlib import PasswordHash
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import User, get_database_session

SESSION_COOKIE = "contractguard_session"
SESSION_DURATION = timedelta(minutes=15)
password_hash = PasswordHash.recommended()
DUMMY_PASSWORD_HASH = password_hash.hash("invalid-password-for-timing-only")
DatabaseSession: TypeAlias = Annotated[AsyncSession, Depends(get_database_session)]


class Credentials(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)

    @field_validator("password")
    @classmethod
    def password_byte_limit(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 1024:
            raise ValueError("Password is too long.")
        return value


class UserResponse(BaseModel):
    id: uuid.UUID
    email: EmailStr
    is_admin: bool = False


def validate_auth_configuration() -> None:
    secret = (os.getenv("AUTH_SECRET_KEY") or "").strip()
    if len(secret.encode("utf-8")) < 32:
        raise RuntimeError("AUTH_SECRET_KEY must be configured with at least 32 characters.")


def is_admin_user(user: User) -> bool:
    admin_email = (os.getenv("ADMIN_EMAIL") or "").strip().casefold()
    return bool(admin_email) and user.email.casefold() == admin_email


def set_session_cookie(response: Response, user_id: uuid.UUID) -> None:
    now = datetime.now(UTC)
    token = jwt.encode(
        {"sub": str(user_id), "iat": now, "exp": now + SESSION_DURATION},
        os.environ["AUTH_SECRET_KEY"],
        algorithm="HS256",
    )
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        max_age=int(SESSION_DURATION.total_seconds()),
        httponly=True,
        secure=(os.getenv("SESSION_COOKIE_SECURE") or "").lower() in {"1", "true", "yes"},
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        key=SESSION_COOKIE,
        httponly=True,
        secure=(os.getenv("SESSION_COOKIE_SECURE") or "").lower() in {"1", "true", "yes"},
        samesite="lax",
        path="/",
    )


async def get_current_user(
    session: DatabaseSession,
    response: Response,
    session_token: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
) -> User:
    if not session_token:
        raise HTTPException(status_code=401, detail="Sign in to continue.")
    try:
        payload = jwt.decode(
            session_token,
            os.environ["AUTH_SECRET_KEY"],
            algorithms=["HS256"],
            options={"require": ["sub", "exp"]},
        )
        user_id = uuid.UUID(payload["sub"])
    except (jwt.InvalidTokenError, KeyError, ValueError):
        raise HTTPException(status_code=401, detail="Your session is invalid or expired.") from None
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="Your account no longer exists.")
    set_session_cookie(response, user.id)
    return user


CurrentUser: TypeAlias = Annotated[User, Depends(get_current_user)]


async def require_admin(user: CurrentUser) -> User:
    if not is_admin_user(user):
        raise HTTPException(status_code=403, detail="Administrator access is required.")
    return user


AdminUser: TypeAlias = Annotated[User, Depends(require_admin)]
