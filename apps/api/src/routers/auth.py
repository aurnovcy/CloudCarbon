"""
Authentication router.

Endpoints:
  POST /auth/token    — exchange OAuth2 code for JWT pair
  POST /auth/refresh  — refresh access token using refresh token
  POST /auth/logout   — invalidate refresh token
  GET  /auth/me       — return current user profile
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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

import redis.asyncio as aioredis

logger = structlog.get_logger(__name__)
router = APIRouter()

_REFRESH_TOKEN_KEY_PREFIX = "refresh_token:"


# ---------------------------------------------------------------------------
# POST /auth/token
# ---------------------------------------------------------------------------

@router.post("/token", response_model=TokenResponse, status_code=status.HTTP_200_OK)
async def exchange_token(
    request: OAuthCodeRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
) -> TokenResponse:
    """
    Exchange an OAuth2 authorization code for a CloudCarbon JWT pair.
    Supports Google, Azure AD, and Okta.
    """
    try:
        user_info = await exchange_code_for_user_info(
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

    # Find or create user
    stmt = select(User).where(
        User.email == user_info.email,
        User.auth_provider == request.provider,
    )
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if user is None:
        # Auto-provision: first OAuth login creates the user.
        # In production, tenant assignment would be handled via invite flow.
        # For now, raise 404 to indicate the user must be provisioned first.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found. Please contact your administrator to provision access.",
        )

    # Update last login
    user.last_login = datetime.now(tz=timezone.utc)
    await db.flush()

    access_token, expires_in = create_access_token(user.id, user.tenant_id, user.role)
    refresh_token, refresh_ttl, jti = create_refresh_token(user.id, user.tenant_id, user.role)

    # Store refresh token JTI in Redis with TTL
    await redis.setex(
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
async def refresh_token(
    request: RefreshTokenRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
) -> AccessTokenResponse:
    """
    Exchange a valid refresh token for a new access token.
    The refresh token's JTI must be present in Redis (not expired or revoked).
    """
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

    # Verify JTI exists in Redis
    stored = await redis.get(f"{_REFRESH_TOKEN_KEY_PREFIX}{payload.jti}")
    if stored is None:
        raise credentials_exception

    # Fetch user to get current role (may have changed)
    user_id = uuid.UUID(payload.sub)
    stmt = select(User).where(User.id == user_id)
    result = await db.execute(stmt)
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
async def logout(
    request: RefreshTokenRequest,
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
) -> None:
    """
    Invalidate a refresh token by removing its JTI from Redis.
    Also adds the JTI to the blacklist so in-flight access tokens are rejected.
    """
    try:
        payload = decode_token(request.refresh_token)
    except JWTError:
        # Silently succeed — token is already invalid
        return

    if payload.jti:
        # Remove from active refresh tokens
        await redis.delete(f"{_REFRESH_TOKEN_KEY_PREFIX}{payload.jti}")
        # Add to blacklist until expiry
        remaining_ttl = payload.exp - int(datetime.now(tz=timezone.utc).timestamp())
        if remaining_ttl > 0:
            await redis.setex(f"blacklist:jti:{payload.jti}", remaining_ttl, "1")

    logger.info("User logged out", user_id=payload.sub)


# ---------------------------------------------------------------------------
# GET /auth/me
# ---------------------------------------------------------------------------

@router.get("/me", response_model=CurrentUserResponse)
async def get_me(
    current_user: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> CurrentUserResponse:
    """Return the authenticated user's profile."""
    stmt = select(User).where(User.id == current_user.id)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    return CurrentUserResponse.model_validate(user)
