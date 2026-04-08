"""
Authentication router (synchronous).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Annotated

import redis
import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.database import get_db
from src.dependencies.auth import CurrentUser, CurrentUserDep, get_current_user
from src.models.user import User
from src.redis_client import get_redis
from src.schemas.auth import (
    AccessTokenResponse,
    CurrentUserResponse,
    OAuthCodeRequest,
    RefreshTokenRequest,
    TokenResponse,
)
from src.services.jwt_service import (
    create_access_token,
    create_refresh_token,
    decode_token,
    get_token_type,
)
from src.services.oauth_service import exchange_code_for_user_info

logger = structlog.get_logger(__name__)
router = APIRouter()

_REFRESH_TOKEN_KEY_PREFIX = "refresh_token:"


# ---------------------------------------------------------------------------
# POST /auth/token
# ---------------------------------------------------------------------------

@router.post("/token", response_model=TokenResponse, status_code=status.HTTP_200_OK)
def exchange_token(
    request: OAuthCodeRequest,
    db: Annotated[Session, Depends(get_db)],
    redis_client: Annotated[redis.Redis, Depends(get_redis)],
) -> TokenResponse:
    """Exchange an OAuth2 authorization code for a CloudCarbon JWT pair."""
    try:
        user_info = exchange_code_for_user_info(
            provider=request.provider,
            code=request.code,
            redirect_uri=request.redirect_uri,
        )
    except Exception as exc:
        logger.warning("OAuth code exchange failed", provider=request.provider, error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="OAuth code exchange failed",
        ) from exc

    stmt = select(User).where(
        User.email == user_info.email,
        User.auth_provider == request.provider,
    )
    result = db.execute(stmt)
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found. Please contact your administrator to provision access.",
        )

    user.last_login = datetime.now(tz=timezone.utc)
    db.flush()

    access_token, expires_in = create_access_token(user.id, user.tenant_id, user.role)
    refresh_token, refresh_ttl, jti = create_refresh_token(user.id, user.tenant_id, user.role)

    redis_client.setex(
        f"{_REFRESH_TOKEN_KEY_PREFIX}{jti}",
        refresh_ttl,
        str(user.id),
    )

    logger.info("User authenticated", user_id=str(user.id), provider=request.provider)

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        expires_in=expires_in,
    )


# ---------------------------------------------------------------------------
# POST /auth/refresh
# ---------------------------------------------------------------------------

@router.post("/refresh", response_model=AccessTokenResponse, status_code=status.HTTP_200_OK)
def refresh_token(
    request: RefreshTokenRequest,
    db: Annotated[Session, Depends(get_db)],
    redis_client: Annotated[redis.Redis, Depends(get_redis)],
) -> AccessTokenResponse:
    """Exchange a valid refresh token for a new access token."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired refresh token",
    )

    try:
        payload = decode_token(request.refresh_token)
    except JWTError:
        raise credentials_exception

    if get_token_type(request.refresh_token) != "refresh":
        raise credentials_exception

    if payload.jti is None:
        raise credentials_exception

    stored = redis_client.get(f"{_REFRESH_TOKEN_KEY_PREFIX}{payload.jti}")
    if stored is None:
        raise credentials_exception

    user_id = uuid.UUID(payload.sub)
    stmt = select(User).where(User.id == user_id)
    result = db.execute(stmt)
    user = result.scalar_one_or_none()

    if user is None:
        raise credentials_exception

    access_token, expires_in = create_access_token(user.id, user.tenant_id, user.role)

    return AccessTokenResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in=expires_in,
    )


# ---------------------------------------------------------------------------
# POST /auth/logout
# ---------------------------------------------------------------------------

@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: RefreshTokenRequest,
    redis_client: Annotated[redis.Redis, Depends(get_redis)],
) -> None:
    """Invalidate a refresh token by removing its JTI from Redis."""
    try:
        payload = decode_token(request.refresh_token)
    except JWTError:
        return

    if payload.jti:
        redis_client.delete(f"{_REFRESH_TOKEN_KEY_PREFIX}{payload.jti}")
        remaining_ttl = payload.exp - int(datetime.now(tz=timezone.utc).timestamp())
        if remaining_ttl > 0:
            redis_client.setex(f"blacklist:jti:{payload.jti}", remaining_ttl, "1")

    logger.info("User logged out", user_id=payload.sub)


# ---------------------------------------------------------------------------
# GET /auth/me
# ---------------------------------------------------------------------------

@router.get("/me", response_model=CurrentUserResponse)
def get_me(
    current_user: CurrentUserDep,
    db: Annotated[Session, Depends(get_db)],
) -> CurrentUserResponse:
    """Return the authenticated user's profile."""
    stmt = select(User).where(User.id == current_user.id)
    result = db.execute(stmt)
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    return CurrentUserResponse.model_validate(user)
