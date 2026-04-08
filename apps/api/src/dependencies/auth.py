"""
FastAPI authentication dependencies (synchronous).

Provides:
  - get_current_user()  — validates JWT or API key, returns CurrentUser
  - require_role()      — RBAC enforcement with role hierarchy
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Annotated

import redis
from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.database import get_db
from src.models.user import User
from src.redis_client import get_redis
from src.services.api_key_service import API_KEY_PREFIX, get_api_key_by_raw
from src.services.jwt_service import decode_token

# ---------------------------------------------------------------------------
# Security schemes
# ---------------------------------------------------------------------------

_bearer_scheme = HTTPBearer(auto_error=False)
_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

# ---------------------------------------------------------------------------
# Role hierarchy
# ---------------------------------------------------------------------------

ROLE_HIERARCHY: dict[str, int] = {
    "viewer": 1,
    "analyst": 2,
    "engineer": 3,
    "admin": 4,
}


# ---------------------------------------------------------------------------
# CurrentUser dataclass
# ---------------------------------------------------------------------------

@dataclass
class CurrentUser:
    id: uuid.UUID
    tenant_id: uuid.UUID
    email: str
    name: str | None
    role: str
    auth_provider: str | None


# ---------------------------------------------------------------------------
# Core dependency: get_current_user
# ---------------------------------------------------------------------------

def get_current_user(
    bearer: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
    api_key: Annotated[str | None, Security(_api_key_header)],
    db: Annotated[Session, Depends(get_db)],
    redis_client: Annotated[redis.Redis, Depends(get_redis)],
) -> CurrentUser:
    """
    Authenticate the request via JWT Bearer token or X-API-Key header.
    Raises HTTP 401 if neither credential is valid.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    # -----------------------------------------------------------------------
    # Path 1: JWT Bearer token
    # -----------------------------------------------------------------------
    if bearer is not None:
        try:
            payload = decode_token(bearer.credentials)
        except JWTError:
            raise credentials_exception

        # Check refresh token blacklist in Redis
        if payload.jti:
            blacklisted = redis_client.get(f"blacklist:jti:{payload.jti}")
            if blacklisted:
                raise credentials_exception

        user_id = uuid.UUID(payload.sub)
        stmt = select(User).where(User.id == user_id)
        result = db.execute(stmt)
        user = result.scalar_one_or_none()

        if user is None:
            raise credentials_exception

        return CurrentUser(
            id=user.id,
            tenant_id=user.tenant_id,
            email=user.email,
            name=user.name,
            role=user.role,
            auth_provider=user.auth_provider,
        )

    # -----------------------------------------------------------------------
    # Path 2: API key (X-API-Key header)
    # -----------------------------------------------------------------------
    if api_key is not None and api_key.startswith(API_KEY_PREFIX):
        key_record = get_api_key_by_raw(db, api_key)
        if key_record is None:
            raise credentials_exception

        stmt = select(User).where(User.id == key_record.user_id)
        result = db.execute(stmt)
        user = result.scalar_one_or_none()

        if user is None:
            raise credentials_exception

        return CurrentUser(
            id=user.id,
            tenant_id=user.tenant_id,
            email=user.email,
            name=user.name,
            role=user.role,
            auth_provider="api_key",
        )

    raise credentials_exception


# ---------------------------------------------------------------------------
# RBAC dependency factory: require_role
# ---------------------------------------------------------------------------

def require_role(minimum_role: str):
    """
    Returns a FastAPI dependency that enforces a minimum role.

    Role hierarchy: admin > engineer > analyst > viewer
    """
    required_level = ROLE_HIERARCHY.get(minimum_role, 0)

    def _check_role(
        current_user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> CurrentUser:
        user_level = ROLE_HIERARCHY.get(current_user.role, 0)
        if user_level < required_level:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Insufficient permissions. Required role: {minimum_role}",
            )
        return current_user

    return _check_role


# ---------------------------------------------------------------------------
# Convenience type aliases
# ---------------------------------------------------------------------------

CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]
AdminDep = Annotated[CurrentUser, Depends(require_role("admin"))]
EngineerDep = Annotated[CurrentUser, Depends(require_role("engineer"))]
AnalystDep = Annotated[CurrentUser, Depends(require_role("analyst"))]
