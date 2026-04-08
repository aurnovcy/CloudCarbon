"""
Alembic environment configuration for CloudCarbon (synchronous).
"""
from __future__ import annotations

import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# ---------------------------------------------------------------------------
# Make src package importable
# ---------------------------------------------------------------------------
_here = os.path.dirname(os.path.abspath(__file__))
_api_dir = os.path.dirname(_here)          # apps/api
_src_dir = os.path.join(_api_dir, "src")   # apps/api/src

for _p in (_api_dir, _src_dir):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Also add local packages
_project_root = os.path.abspath(os.path.join(_api_dir, "..", ".."))
for _pkg in ("packages/carbon-models/src", "packages/focus-schema/src"):
    _full = os.path.join(_project_root, _pkg)
    if os.path.isdir(_full) and _full not in sys.path:
        sys.path.insert(0, _full)

# Import Base and all models so autogenerate discovers the full schema
from src.database import Base  # noqa: E402
import src.models  # noqa: F401, E402 — registers all models against Base

# ---------------------------------------------------------------------------
# Alembic Config
# ---------------------------------------------------------------------------
config = context.config

# Allow DATABASE_URL env var to override alembic.ini
_db_url = os.getenv("DATABASE_URL", "")
if _db_url:
    _sync_url = (
        _db_url
        .replace("postgresql+asyncpg://", "postgresql+psycopg2://")
        .replace("postgresql://", "postgresql+psycopg2://")
    )
    config.set_main_option("sqlalchemy.url", _sync_url)
else:
    # Ensure the ini URL uses psycopg2
    _ini_url = config.get_main_option("sqlalchemy.url") or ""
    _ini_url = (
        _ini_url
        .replace("postgresql+asyncpg://", "postgresql+psycopg2://")
        .replace("postgresql://", "postgresql+psycopg2://")
    )
    if _ini_url:
        config.set_main_option("sqlalchemy.url", _ini_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


# ---------------------------------------------------------------------------
# Offline mode
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
# Online mode (synchronous)
# ---------------------------------------------------------------------------

def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
