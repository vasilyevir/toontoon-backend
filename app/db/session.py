"""Async engine and session management.

Mirrors ``app/redis_client.py``: a single engine for the whole process, created
on startup and disposed on shutdown, so nothing holds a connection pool that
outlives the app.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

_engine: Optional[AsyncEngine] = None
_session_factory: Optional[async_sessionmaker[AsyncSession]] = None


async def connect() -> AsyncEngine:
    """Create the engine and verify the database answers. Fails fast on startup."""
    global _engine, _session_factory
    if _engine is None:
        _engine = create_async_engine(
            settings.database_url,
            echo=settings.database_echo,
            pool_size=settings.database_pool_size,
            max_overflow=settings.database_max_overflow,
            pool_pre_ping=True,  # a connection killed by the DB must not surface as a 500
        )
        _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
        async with _engine.connect():
            pass
    return _engine


async def disconnect() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None


def get_factory() -> async_sessionmaker[AsyncSession]:
    if _session_factory is None:
        raise RuntimeError("Database is not initialised. Did the app lifespan run?")
    return _session_factory


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one session per request, committed on success.

    A handler that raises leaves the transaction rolled back — which is exactly
    what we want for the wallet, where a half-applied charge is worse than none.
    """
    async with get_factory()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Same contract as ``get_session`` for code outside a request — background
    video jobs, App Store notification handling, scheduled cleanups."""
    async with get_factory()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
