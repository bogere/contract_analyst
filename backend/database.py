"""PostgreSQL models and async database session management."""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Literal

from fastapi import HTTPException
from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    reports: Mapped[list[AnalysisReport]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class AnalysisReport(Base):
    __tablename__ = "analysis_reports"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    filename: Mapped[str] = mapped_column(String(255))
    result: Mapped[dict[str, object]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    user: Mapped[User] = relationship(back_populates="reports")


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    plan_key: Mapped[str] = mapped_column(String(32))
    paypal_subscription_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    approval_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    status: Mapped[str] = mapped_column(String(32))
    next_billing_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_webhook_event_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class WorkspaceSettings(Base):
    __tablename__ = "workspace_settings"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default="global")
    model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    base_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    encrypted_api_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    language: Mapped[Literal["en", "zh"]] = mapped_column(String(8), default="en")
    jurisdiction: Mapped[Literal["auto", "cn", "us-ca"]] = mapped_column(String(16), default="auto")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _database_url() -> str:
    raw_url = (os.getenv("DATABASE_URL") or "").strip()
    if not raw_url:
        raise RuntimeError("DATABASE_URL is required to start the API.")
    url = make_url(raw_url)
    if url.drivername in {"postgres", "postgresql"}:
        url = url.set(drivername="postgresql+asyncpg")
    if url.drivername != "postgresql+asyncpg":
        raise RuntimeError("DATABASE_URL must use PostgreSQL (postgresql:// or postgresql+asyncpg://).")
    return url.render_as_string(hide_password=False)


async def initialize_database() -> None:
    global _engine, _session_factory
    _engine = create_async_engine(_database_url(), pool_pre_ping=True)
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    async with _engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


async def close_database() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None


async def get_database_session() -> AsyncIterator[AsyncSession]:
    if _session_factory is None:
        raise HTTPException(status_code=503, detail="Database is not initialized.")
    async with _session_factory() as session:
        yield session
