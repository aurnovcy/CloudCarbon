"""
Pydantic schemas for authentication endpoints.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, field_validator


# ---------------------------------------------------------------------------
# Token schemas
# ---------------------------------------------------------------------------

class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int  # seconds


class RefreshTokenRequest(BaseModel):
    refresh_token: str


class AccessTokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int


# ---------------------------------------------------------------------------
# OAuth2 code exchange
# ---------------------------------------------------------------------------

class OAuthCodeRequest(BaseModel):
    provider: Literal["google", "azure_ad", "okta"]
    code: str
    redirect_uri: str
    state: str | None = None


# ---------------------------------------------------------------------------
# Current user
# ---------------------------------------------------------------------------

class CurrentUserResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    email: str
    name: str | None
    role: str
    auth_provider: str | None
    created_at: datetime
    last_login: datetime | None

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# API key schemas
# ---------------------------------------------------------------------------

class CreateApiKeyRequest(BaseModel):
    name: str
    expires_at: datetime | None = None


class CreateApiKeyResponse(BaseModel):
    id: uuid.UUID
    name: str
    key: str  # Raw key — shown ONCE, never stored
    created_at: datetime
    expires_at: datetime | None

    model_config = {"from_attributes": True}


class ApiKeyListItem(BaseModel):
    id: uuid.UUID
    name: str
    last_used_at: datetime | None
    expires_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# JWT payload (internal use)
# ---------------------------------------------------------------------------

class JWTPayload(BaseModel):
    sub: str          # user_id as string
    tenant_id: str
    role: str
    exp: int          # Unix timestamp
    iat: int          # Issued at
    jti: str | None = None  # JWT ID (for refresh tokens)
