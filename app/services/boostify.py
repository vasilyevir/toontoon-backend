"""Boostyfi client — identity + wallet provider (v2).

Boostyfi is not live yet, so every method works in two modes:

* ``BOOSTIFY_MOCK=true``  -> returns deterministic fake data so the whole
  TOONTOON flow (login, balance, two-phase payment, transactions) works
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


class PaymentExpiredError(Exception):
    """Raised when Boostyfi returns payment_expired on confirm/cancel.

    This means the 5-minute reserve window elapsed; Boostyfi already returned
    the cap to the grant limit. The caller must decide whether to re-charge
    (on confirm) or silently skip (on cancel).
    """


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
    Tries to extract user claims from ``id_token`` first; falls back to a
    ``/userinfo`` call if the token is missing or its payload is incomplete.
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

        access_token = data["access_token"]

        # Try id_token payload first (avoids an extra HTTP call).
        user = _claims_from_id_token(data.get("id_token", ""))
        if not user.get("sub"):
            # id_token missing or incomplete — fall back to /userinfo.
            ui_resp = await client.get(
                f"{settings.boostify_base_url}/oauth/userinfo",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            ui_resp.raise_for_status()
            ui = ui_resp.json()
            user = {
                "sub": str(ui.get("sub", "")),
                "email": ui.get("email", ""),
                "name": ui.get("name") or ui.get("preferred_username", ""),
                "avatar": ui.get("picture"),
            }

        return {
            "access_token": access_token,
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
        return Balance(available=_MOCK_AVAILABLE, locked=_MOCK_LOCKED, grant_cap=_MOCK_AVAILABLE)

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{settings.boostify_base_url}/sso/toontoon/balance",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        # Wallet API may not be deployed yet (404) — return a zero balance
        # so the UI renders cleanly rather than crashing.
        if resp.status_code == 404:
            return Balance(available=0, locked=0)
        resp.raise_for_status()
        data = resp.json()

        # All numeric fields arrive as high-precision decimal strings, e.g.
        # "999999.000000000000000000" or "0E-18" — float() handles both.
        def _int(v) -> int:
            try:
                return max(0, int(float(str(v))))
            except (TypeError, ValueError):
                return 0

        grant_remaining = _int(data.get("grant_remaining", 0))
        grant_cap       = _int(data.get("grant_cap", 0))
        locked          = _int(data.get("locked", 0))
        grant_expires_at = data.get("grant_expires_at")  # ISO-8601 str or None

        # ``grant_remaining`` is the real spending limit inside Toontoon.
        # The raw ``available`` (user's total holdings, e.g. 999999) is
        # misleading in the product UI — we expose grant_remaining instead.
        return Balance(
            available=grant_remaining,
            locked=locked,
            grant_cap=grant_cap,
            grant_expires_at=grant_expires_at,
        )


async def create_payment(access_token: str, *, amount: int, reason: str) -> Payment:
    """Phase 1 — reserve funds. user_id is NOT sent; Boostyfi reads it from the token."""
    if settings.boostify_mock:
        return Payment(payment_id="pay_" + new_id(), status=PaymentStatus.PENDING, amount=amount)

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{settings.boostify_base_url}/sso/toontoon/payment/create",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"amount": amount, "reason": reason},
        )
        if resp.status_code == 404:
            raise RuntimeError("Boostyfi Wallet API is not yet deployed — payment creation unavailable")
        if resp.status_code == 400:
            err = resp.json().get("error", "")
            if err in ("insufficient_balance", "cap_exceeded"):
                from app.services.wallet import InsufficientFunds  # local import to avoid circular
                raise InsufficientFunds(err)
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
            f"{settings.boostify_base_url}/sso/toontoon/payment/confirm",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"payment_id": payment_id},
        )
        # payment_expired (400) means the 5-min TTL elapsed; Boostyfi already
        # returned the cap. Raise a typed exception so the caller can re-charge.
        if resp.status_code == 400:
            err = resp.json().get("error", "")
            if err == "payment_expired":
                raise PaymentExpiredError(f"Payment {payment_id} expired before confirmation")
        resp.raise_for_status()
        data = resp.json()
        return Payment(payment_id=payment_id, status=PaymentStatus(data["status"]), amount=0)


async def cancel_payment(access_token: str, payment_id: str) -> Payment:
    if settings.boostify_mock:
        return Payment(payment_id=payment_id, status=PaymentStatus.CANCELLED, amount=0)

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{settings.boostify_base_url}/sso/toontoon/payment/cancel",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"payment_id": payment_id},
        )
        # payment_expired / payment_not_pending: Boostyfi already closed this
        # reserve automatically — treat as successfully cancelled, nothing to do.
        if resp.status_code == 400:
            err = resp.json().get("error", "")
            if err in ("payment_expired", "payment_not_pending"):
                return Payment(payment_id=payment_id, status=PaymentStatus.CANCELLED, amount=0)
        resp.raise_for_status()
        data = resp.json()
        return Payment(payment_id=payment_id, status=PaymentStatus(data["status"]), amount=0)


async def get_transactions(access_token: str, *, limit: int = 20, offset: int = 0) -> list[dict]:
    if settings.boostify_mock:
        now = int(time.time())
        return [
            {"payment_id": "pay_mock_1", "amount": -1, "reason": "toontoon:image_generate",
             "status": "confirmed", "created_at": now - 3600},
            {"payment_id": "pay_mock_2", "amount": -2, "reason": "toontoon:video_generate",
             "status": "confirmed", "created_at": now - 7200},
        ]

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{settings.boostify_base_url}/sso/toontoon/transactions",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"limit": limit, "offset": offset},
        )
        resp.raise_for_status()
        return resp.json()
