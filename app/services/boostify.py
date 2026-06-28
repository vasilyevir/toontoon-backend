"""Boostyfi client — identity + wallet provider (v2).

Boostyfi is not live yet, so every method works in two modes:

* ``BOOSTIFY_MOCK=true``  -> returns deterministic fake data so the whole
  ARTEKI flow (login, balance, two-phase payment, transactions) works
  end-to-end without a real Boostyfi backend.
* ``BOOSTIFY_MOCK=false`` -> performs the real HTTP calls against
  ``BOOSTIFY_BASE_URL`` (https://api.boostyfi.com/api/v1) using the OAuth
  client credentials.

When Boostyfi ships, only this module changes; routers stay the same.

PKCE flow (required by Boostyfi):
  1. ``pkce_pair()``         → generate (code_verifier, code_challenge)
  2. ``authorize_url()``     → embed code_challenge in the redirect URL
  3. Store code_verifier in Redis keyed by ``state``
  4. ``exchange_code()``     → send code_verifier in the token request
"""
from __future__ import annotations

import base64
import hashlib
import secrets
import time

import httpx

from app.config import settings
from app.core.security import new_id
from app.models.payment import Balance, Payment, PaymentStatus

# Open question for the Boostyfi team: are locked tokens spendable inside the
# product? We assume YES here for the mock.
_MOCK_AVAILABLE = 200
_MOCK_LOCKED = 1000


# ─── PKCE helpers ─────────────────────────────────────────────────────────────

def pkce_pair() -> tuple[str, str]:
    """Return ``(code_verifier, code_challenge)`` using S256 method.

    ``code_verifier``  — random URL-safe string, 64 chars (well within 43–128).
    ``code_challenge`` — BASE64URL(SHA256(verifier)), no padding.
    """
    verifier = secrets.token_urlsafe(48)  # 48 bytes → 64 base64url chars
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


# ─── OAuth URLs / token exchange ──────────────────────────────────────────────

def authorize_url(state: str, code_challenge: str) -> str:
    """Browser-facing OAuth authorize URL (PKCE required by Boostyfi)."""
    from urllib.parse import urlencode
    params = {
        "client_id": settings.boostify_client_id,
        "redirect_uri": settings.boostify_redirect_uri,
        "response_type": "code",
        "scope": "openid email profile wallet",
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    return f"{settings.boostify_base_url}/oauth/authorize?{urlencode(params)}"


async def exchange_code(code: str, code_verifier: str) -> dict:
    """Exchange an authorization code for tokens + user info.

    Body must be application/json (Boostyfi does NOT accept form-urlencoded).
    ``id_token`` claims are used to avoid a separate /userinfo call.
    """
    if settings.boostify_mock:
        return {
            "access_token": "mock_access_" + new_id(),
            "refresh_token": "mock_refresh_" + new_id(),
            "expires_in": 3600,
            "user": {
                "sub": "boostify_" + code[:8],
                "email": "demo@boostyfi.example",
                "name": "Demo User",
                "avatar": None,
            },
        }

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{settings.boostify_base_url}/oauth/token",
            json={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.boostify_redirect_uri,
                "client_id": settings.boostify_client_id,
                "client_secret": settings.boostify_client_secret,
                "code_verifier": code_verifier,
            },
        )
        resp.raise_for_status()
        data = resp.json()

        # Parse user claims from id_token to avoid a separate /userinfo call.
        user = _claims_from_id_token(data.get("id_token", ""))
        return {
            "access_token": data["access_token"],
            "refresh_token": data.get("refresh_token"),
            "expires_in": data.get("expires_in", 3600),
            "user": user,
        }


def _claims_from_id_token(id_token: str) -> dict:
    """Decode the payload of a JWT without verifying the signature.

    We only need the identity claims (sub, email, name) that Boostyfi
    signed — the JWKS verification step is left for a future hardening pass.
    """
    try:
        parts = id_token.split(".")
        if len(parts) < 2:
            return {}
        # JWT payload is base64url-encoded; pad to a multiple of 4.
        padded = parts[1] + "=" * (4 - len(parts[1]) % 4)
        import json as _json
        payload = _json.loads(base64.urlsafe_b64decode(padded))
        return {
            "sub": str(payload.get("sub", "")),
            "email": payload.get("email", ""),
            "name": payload.get("name") or payload.get("preferred_username", ""),
            "avatar": payload.get("picture"),
        }
    except Exception:
        return {}


async def refresh(refresh_token: str) -> dict:
    """Silently refresh an expired access token (body must be JSON)."""
    if settings.boostify_mock:
        return {
            "access_token": "mock_access_" + new_id(),
            "refresh_token": refresh_token,
            "expires_in": 3600,
        }

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{settings.boostify_base_url}/oauth/token",
            json={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": settings.boostify_client_id,
                "client_secret": settings.boostify_client_secret,
            },
        )
        resp.raise_for_status()
        return resp.json()


# ─── Wallet API ───────────────────────────────────────────────────────────────

async def get_balance(access_token: str) -> Balance:
    if settings.boostify_mock:
        return Balance(available=_MOCK_AVAILABLE, locked=_MOCK_LOCKED)

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{settings.boostify_base_url}/sso/arteki/balance",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        resp.raise_for_status()
        data = resp.json()
        # ``available`` and ``locked`` are returned as decimal strings ("45.0").
        return Balance(
            available=int(float(data["available"])),
            locked=int(float(data.get("locked", 0))),
        )


async def create_payment(access_token: str, *, amount: int, reason: str) -> Payment:
    """Phase 1 — reserve funds. user_id is NOT sent; Boostyfi reads it from the token."""
    if settings.boostify_mock:
        return Payment(payment_id="pay_" + new_id(), status=PaymentStatus.PENDING, amount=amount)

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{settings.boostify_base_url}/sso/arteki/payment/create",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"amount": amount, "reason": reason},
        )
        resp.raise_for_status()
        data = resp.json()
        return Payment(
            payment_id=data["payment_id"],
            status=PaymentStatus(data["status"]),
            amount=amount,
        )


async def confirm_payment(access_token: str, payment_id: str) -> Payment:
    if settings.boostify_mock:
        return Payment(payment_id=payment_id, status=PaymentStatus.CONFIRMED, amount=0)

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{settings.boostify_base_url}/sso/arteki/payment/confirm",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"payment_id": payment_id},
        )
        resp.raise_for_status()
        data = resp.json()
        return Payment(payment_id=payment_id, status=PaymentStatus(data["status"]), amount=0)


async def cancel_payment(access_token: str, payment_id: str) -> Payment:
    if settings.boostify_mock:
        return Payment(payment_id=payment_id, status=PaymentStatus.CANCELLED, amount=0)

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{settings.boostify_base_url}/sso/arteki/payment/cancel",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"payment_id": payment_id},
        )
        resp.raise_for_status()
        data = resp.json()
        return Payment(payment_id=payment_id, status=PaymentStatus(data["status"]), amount=0)


async def get_transactions(access_token: str, *, limit: int = 20, offset: int = 0) -> list[dict]:
    if settings.boostify_mock:
        now = int(time.time())
        return [
            {"payment_id": "pay_mock_1", "amount": -1, "reason": "arteki:image_generate",
             "status": "confirmed", "created_at": now - 3600},
            {"payment_id": "pay_mock_2", "amount": -2, "reason": "arteki:video_generate",
             "status": "confirmed", "created_at": now - 7200},
        ]

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{settings.boostify_base_url}/sso/arteki/transactions",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"limit": limit, "offset": offset},
        )
        resp.raise_for_status()
        return resp.json()
