"""Boostify webhooks — asynchronous payment confirmation."""
from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Request, status

from app.config import settings
from app.core.security import verify_signature
from app.redis_client import get_client

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


@router.post("/boostify")
async def boostify_webhook(
    request: Request,
    x_boostify_signature: str | None = Header(default=None, alias="X-Boostify-Signature"),
):
    """Receive ``payment.confirmed`` (and similar) events from Boostify.

    Verifies the HMAC-SHA256 signature over the raw body, then refreshes any
    cached balance for the affected user.
    """
    body = await request.body()
    if not x_boostify_signature or not verify_signature(settings.boostify_webhook_secret, body, x_boostify_signature):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid signature")

    event = await request.json()
    event_type = event.get("event")
    user_id = event.get("user_id")

    if event_type in {"payment.confirmed", "payment.cancelled"} and user_id:
        # Balance is always read live from Boostify, so we just drop any cache.
        redis = get_client()
        await redis.delete(f"balance_cache:{user_id}")

    return {"ok": True}
