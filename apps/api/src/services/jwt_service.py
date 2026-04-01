"""
JWT service — creates, signs, and validates access and refresh tokens.
Uses python-jose with HS256 by default; RS256 is supported by swapping the secret
for an RSA private key in production.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt

from src.config import get_settings
from src.schemas.auth import JWTPayload

settings = get_settings()


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


def create_access_token(
    user_id: uuid.UUID,
    tenant_id: uuid.UUID,
    role: str,
) -> tuple[str, int]:
    """
    Create a signed JWT access token.

    Returns:
        (token_string, expires_in_seconds)
    """
    expire_minutes = settings.jwt_access_token_expire_minutes
    now = _utcnow()
    expire = now + timedelta(minutes=expire_minutes)

    payload: dict[str, Any] = {
        "sub": str(user_id),
        "tenant_id": str(tenant_id),
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
        "type": "access",
    }

    token = jwt.encode(
        payload,
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )
    return token, expire_minutes * 60


def create_refresh_token(
    user_id: uuid.UUID,
    tenant_id: uuid.UUID,
    role: str,
) -> tuple[str, int, str]:
    """
    Create a signed JWT refresh token with a unique JTI for Redis storage.

    Returns:
        (token_string, expires_in_seconds, jti)
    """
    expire_days = settings.jwt_refresh_token_expire_days
    now = _utcnow()
    expire = now + timedelta(days=expire_days)
    jti = str(uuid.uuid4())

    payload: dict[str, Any] = {
        "sub": str(user_id),
        "tenant_id": str(tenant_id),
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
        "jti": jti,
        "type": "refresh",
    }

    token = jwt.encode(
        payload,
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )
    return token, expire_days * 86400, jti


def decode_token(token: str) -> JWTPayload:
    """
    Decode and validate a JWT token.

    Raises:
        jose.JWTError: if the token is invalid or expired.
    """
    payload = jwt.decode(
        token,
        settings.jwt_secret_key,
        algorithms=[settings.jwt_algorithm],
    )
    return JWTPayload(
        sub=payload["sub"],
        tenant_id=payload["tenant_id"],
        role=payload["role"],
        exp=payload["exp"],
        iat=payload["iat"],
        jti=payload.get("jti"),
    )


def get_token_type(token: str) -> str:
    """Extract the token type claim without full validation."""
    payload = jwt.decode(
        token,
        settings.jwt_secret_key,
        algorithms=[settings.jwt_algorithm],
    )
    return payload.get("type", "access")
