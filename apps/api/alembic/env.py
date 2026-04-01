"""
Alembic environment configuration for CloudCarbon.
Supports both offline (SQL generation) and online (live DB) migration modes.
"""
from __future__ import annotations

import asyncio
import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# ---------------------------------------------------------------------------
# Make the src package importable
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Import Base and all models so autogenerate can discover the full schema.
# We import Base directly from the ORM base module to avoid triggering
# the async engine creation in database.py at import time.
from sqlalchemy.orm import DeclarativeBase

class _Base(DeclarativeBase):
    pass

# Now import all models — they register against their own Base
# We need to import them so their metadata is populated
import importlib, src.models as _models_pkg  # noqa: E402, F401

# Resolve the actual Base used by the models
from src.database import Base  # noqa: E402

# ---------------------------------------------------------------------------
# Alembic Config
# ---------------------------------------------------------------------------
config = context.config

# Allow DATABASE_URL env var to override alembic.ini
_db_url = os.getenv("DATABASE_URL", "")
if _db_url:
    # Use sync psycopg2 driver for offline/autogenerate
    _sync_url = _db_url.replace("postgresql+asyncpg://", "postgresql://")
    config.set_main_option("sqlalchemy.url", _sync_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


# ---------------------------------------------------------------------------
# Offline mode — emit SQL to stdout without a live connection
# ---------------------------------------------------------------------------

def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


# ---------------------------------------------------------------------------
# Online mode — connect to a live database (async)
# ---------------------------------------------------------------------------

def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    _url = config.get_main_option("sqlalchemy.url", "").replace(
        "postgresql://", "postgresql+asyncpg://"
    )
    connectable = async_engine_from_config(
        {"sqlalchemy.url": _url},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
