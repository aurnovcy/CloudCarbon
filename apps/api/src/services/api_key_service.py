"""
API key service — generates, hashes, and validates cc_-prefixed API keys.
Raw keys are shown once at creation and never stored; only the bcrypt hash is persisted.
"""
from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timezone

from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.api_key import ApiKey

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

API_KEY_PREFIX = "cc_"
API_KEY_BYTES = 32  # 256-bit entropy


def generate_raw_key() -> str:
    """Generate a new raw API key with the cc_ prefix."""
    return API_KEY_PREFIX + secrets.token_urlsafe(API_KEY_BYTES)


def hash_key(raw_key: str) -> str:
    """Hash a raw API key using bcrypt."""
    return _pwd_context.hash(raw_key)


def verify_key(raw_key: str, key_hash: str) -> bool:
    """Verify a raw API key against its bcrypt hash."""
    return _pwd_context.verify(raw_key, key_hash)


async def create_api_key(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    name: str,
    expires_at: datetime | None = None,
) -> tuple[ApiKey, str]:
    """
    Create and persist a new API key.

    Returns:
        (ApiKey ORM object, raw_key_string)
        The raw key is returned once and must be shown to the user immediately.
    """
    raw_key = generate_raw_key()
    key_hash = hash_key(raw_key)

    api_key = ApiKey(
        tenant_id=tenant_id,
        user_id=user_id,
        key_hash=key_hash,
        name=name,
        expires_at=expires_at,
    )
    db.add(api_key)
    await db.flush()  # Get the ID without committing
    return api_key, raw_key


async def get_api_key_by_raw(
    db: AsyncSession,
    raw_key: str,
) -> ApiKey | None:
    """
    Look up and validate an API key by its raw value.
    Updates last_used_at on success.

    Returns None if the key is not found, expired, or invalid.
    """
    if not raw_key.startswith(API_KEY_PREFIX):
        return None

    # Fetch all non-expired keys (we must check hash for each — bcrypt is not reversible)
    # In production, consider adding a fast lookup prefix column to avoid full table scans
    now = datetime.now(tz=timezone.utc)
    stmt = select(ApiKey).where(
        (ApiKey.expires_at.is_(None)) | (ApiKey.expires_at > now)
    )
    result = await db.execute(stmt)
    keys = result.scalars().all()

    for key in keys:
        if verify_key(raw_key, key.key_hash):
            # Update last_used_at
            key.last_used_at = now
            await db.flush()
            return key

    return None
