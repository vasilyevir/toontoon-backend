"""Google OAuth client — identity only (Sign in with Google).

Used by BOTH the web ("Continue with Google" button, not yet wired on the
frontend) and the native app (opens the same consent screen inside
``ASWebAuthenticationSession``, distinguished by ``?platform=app`` on
``/api/auth/google/login`` — see ``routers/auth.py``).

Trust model: the token exchange is a direct server-to-server HTTPS call to
Google (``https://oauth2.googleapis.com/token``), so — exactly like the
existing Boostyfi client (``services/boostify.py``) — we decode the
``id_token`` payload without re-verifying its signature; the transport
channel is already the trust boundary. This is NOT the same trust model as
Apple's native flow (``apple_oauth.py``), where the token comes from the
client and must be signature-verified.
"""
from __future__ import annotations

import base64
import json
from urllib.parse import urlencode

import httpx

from app.config import settings

_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URL = "https://oauth2.googleapis.com/token"


def authorize_url(state: str) -> str:
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_redirect_uri_effective,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "online",
        "prompt": "select_account",
    }
    return f"{_AUTH_URL}?{urlencode(params)}"


async def exchange_code(code: str) -> dict:
    """Exchange an authorization code for tokens + user claims.

    Returns ``{sub, email, name, avatar}``.
    """
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            _TOKEN_URL,
            data={
                "code": code,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "redirect_uri": settings.google_redirect_uri_effective,
                "grant_type": "authorization_code",
            },
        )
        resp.raise_for_status()
        data = resp.json()

    claims = _decode_id_token(data.get("id_token", ""))
    if not claims.get("sub"):
        raise RuntimeError("Google token response missing id_token/sub")
    return claims


def _decode_id_token(id_token: str) -> dict:
    try:
        parts = id_token.split(".")
        if len(parts) < 2:
            return {}
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        return {
            "sub": str(payload.get("sub", "")),
            "email": payload.get("email"),
            "name": payload.get("name") or payload.get("given_name"),
            "avatar": payload.get("picture"),
        }
    except Exception:
        return {}
