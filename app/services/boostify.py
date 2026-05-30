"""Boostify client — identity + wallet provider (v2).

Boostify is not live yet, so every method works in two modes:

* ``BOOSTIFY_MOCK=true``  -> returns deterministic fake data so the whole
  ARTEKI flow (login, balance, two-phase payment, transactions) works
  end-to-end without a real Boostify backend.
* ``BOOSTIFY_MOCK=false`` -> performs the real HTTP calls against
  ``BOOSTIFY_BASE_URL`` using the OAuth client credentials.

When Boostify ships, only this module changes; routers stay the same.
"""
from __future__ import annotations

import time
from urllib.parse import urlencode

import httpx

from app.config import settings
from app.core.security import new_id
from app.models.payment import Balance, Payment, PaymentStatus

# Open question for the Boostify team (see ТЗ): are locked tokens spendable
# inside the product? We assume YES here for the mock.
_MOCK_AVAILABLE = 200
_MOCK_LOCKED = 1000


def authorize_url(state: str) -> str:
    """Browser-facing OAuth authorize URL."""
    params = {
        "client_id": settings.boostify_client_id,
        "redirect_uri": settings.boostify_redirect_uri,
        "response_type": "code",
        "scope": "openid email profile wallet",
        "state": state,
    }
    return f"{settings.boostify_base_url}/oauth/authorize?{urlencode(params)}"


async def exchange_code(code: str) -> dict:
    """Exchange an authorization code for tokens + user info."""
    if settings.boostify_mock:
        return {
            "access_token": "mock_access_" + new_id(),
            "refresh_token": "mock_refresh_" + new_id(),
            "expires_in": 3600,
            "user": {
                "sub": "boostify_" + code[:8],
                "email": "demo@boostify.example",
                "name": "Demo User",
                "avatar": None,
            },
        }

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{settings.boostify_base_url}/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.boostify_redirect_uri,
                "client_id": settings.boostify_client_id,
                "client_secret": settings.boostify_client_secret,
            },
        )
        resp.raise_for_status()
        return resp.json()


async def refresh(refresh_token: str) -> dict:
    """Silently refresh an expired access token."""
    if settings.boostify_mock:
        return {
            "access_token": "mock_access_" + new_id(),
            "refresh_token": refresh_token,
            "expires_in": 3600,
        }

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{settings.boostify_base_url}/oauth/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": settings.boostify_client_id,
                "client_secret": settings.boostify_client_secret,
            },
        )
        resp.raise_for_status()
        return resp.json()


async def get_balance(access_token: str) -> Balance:
    if settings.boostify_mock:
        return Balance(available=_MOCK_AVAILABLE, locked=_MOCK_LOCKED)

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{settings.boostify_base_url}/user/balance",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        resp.raise_for_status()
        data = resp.json()
        return Balance(available=data["available"], locked=data.get("locked", 0))


async def create_payment(access_token: str, *, user_id: str, amount: int, reason: str) -> Payment:
    if settings.boostify_mock:
        return Payment(payment_id="pay_" + new_id(), status=PaymentStatus.PENDING, amount=amount)

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{settings.boostify_base_url}/payment/create",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"user_id": user_id, "amount": amount, "reason": reason},
        )
        resp.raise_for_status()
        data = resp.json()
        return Payment(payment_id=data["payment_id"], status=PaymentStatus(data["status"]), amount=amount)


async def confirm_payment(access_token: str, payment_id: str) -> Payment:
    if settings.boostify_mock:
        return Payment(payment_id=payment_id, status=PaymentStatus.CONFIRMED, amount=0)

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{settings.boostify_base_url}/payment/confirm",
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
            f"{settings.boostify_base_url}/payment/cancel",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"payment_id": payment_id},
        )
        resp.raise_for_status()
        data = resp.json()
        return Payment(payment_id=payment_id, status=PaymentStatus(data["status"]), amount=0)


async def get_transactions(access_token: str) -> list[dict]:
    if settings.boostify_mock:
        now = int(time.time())
        return [
            {"payment_id": "pay_mock_1", "amount": -50, "reason": "arteki:image_generate", "created_at": now - 3600},
            {"payment_id": "pay_mock_2", "amount": -200, "reason": "arteki:video_generate", "created_at": now - 7200},
        ]

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{settings.boostify_base_url}/transactions",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        resp.raise_for_status()
        return resp.json()
