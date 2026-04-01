"""
Async SQLAlchemy 2.0 engine, session factory, and base declarative model.
"""
from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from src.config import get_settings


class Base(DeclarativeBase):
    """Shared declarative base for all SQLAlchemy models."""
    pass


def _create_engine() -> object:
    settings = get_settings()
    return create_async_engine(
        settings.database_url,
        pool_size=settings.database_pool_size,
        max_overflow=20,
        pool_pre_ping=True,
        echo=(settings.environment == "development"),
    )


engine = _create_engine()

AsyncSessionLocal = async_sessionmaker(
    bind=engine,  # type: ignore[arg-type]
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields a database session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
