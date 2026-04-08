"""
OAuth2 provider service — exchanges authorization codes for user info
from Google, Azure AD, and Okta using Authlib (sync).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from authlib.integrations.httpx_client import OAuth2Client

from src.config import get_settings

settings = get_settings()

OAuthProvider = Literal["google", "azure_ad", "okta"]


@dataclass
class OAuthUserInfo:
    provider: OAuthProvider
    external_id: str
    email: str
    name: str | None


# ---------------------------------------------------------------------------
# Provider configurations
# ---------------------------------------------------------------------------

def _google_client() -> OAuth2Client:
    return OAuth2Client(
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
    )


def _azure_ad_client() -> OAuth2Client:
    return OAuth2Client(
        client_id=settings.azure_ad_client_id,
        client_secret=settings.azure_ad_client_secret,
    )


def _okta_client() -> OAuth2Client:
    return OAuth2Client(
        client_id=settings.okta_client_id,
        client_secret=settings.okta_client_secret,
    )


# ---------------------------------------------------------------------------
# Token exchange
# ---------------------------------------------------------------------------

def exchange_code_for_user_info(
    provider: OAuthProvider,
    code: str,
    redirect_uri: str,
) -> OAuthUserInfo:
    """
    Exchange an OAuth2 authorization code for user profile information.
    """
    if provider == "google":
        return _exchange_google(code, redirect_uri)
    elif provider == "azure_ad":
        return _exchange_azure_ad(code, redirect_uri)
    elif provider == "okta":
        return _exchange_okta(code, redirect_uri)
    else:
        raise ValueError(f"Unsupported OAuth provider: {provider}")


def _exchange_google(code: str, redirect_uri: str) -> OAuthUserInfo:
    token_url = "https://oauth2.googleapis.com/token"
    userinfo_url = "https://www.googleapis.com/oauth2/v3/userinfo"

    with _google_client() as client:
        client.fetch_token(
            token_url,
            code=code,
            redirect_uri=redirect_uri,
            grant_type="authorization_code",
        )
        resp = client.get(userinfo_url)
        resp.raise_for_status()
        data = resp.json()

    return OAuthUserInfo(
        provider="google",
        external_id=data["sub"],
        email=data["email"],
        name=data.get("name"),
    )


def _exchange_azure_ad(code: str, redirect_uri: str) -> OAuthUserInfo:
    tenant_id = settings.azure_ad_tenant_id
    token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    userinfo_url = "https://graph.microsoft.com/v1.0/me"

    with _azure_ad_client() as client:
        client.fetch_token(
            token_url,
            code=code,
            redirect_uri=redirect_uri,
            grant_type="authorization_code",
        )
        resp = client.get(userinfo_url)
        resp.raise_for_status()
        data = resp.json()

    return OAuthUserInfo(
        provider="azure_ad",
        external_id=data["id"],
        email=data.get("mail") or data.get("userPrincipalName", ""),
        name=data.get("displayName"),
    )


def _exchange_okta(code: str, redirect_uri: str) -> OAuthUserInfo:
    domain = settings.okta_domain
    token_url = f"https://{domain}/oauth2/v1/token"
    userinfo_url = f"https://{domain}/oauth2/v1/userinfo"

    with _okta_client() as client:
        client.fetch_token(
            token_url,
            code=code,
            redirect_uri=redirect_uri,
            grant_type="authorization_code",
        )
        resp = client.get(userinfo_url)
        resp.raise_for_status()
        data = resp.json()

    return OAuthUserInfo(
        provider="okta",
        external_id=data["sub"],
        email=data["email"],
        name=data.get("name"),
    )
