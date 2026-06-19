"""Push notification endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.config import settings
from app.deps import Context, required_context
from app.services import push_service

router = APIRouter(prefix="/api/push", tags=["push"])


class SubscribeBody(BaseModel):
    subscription: dict


class UnsubscribeBody(BaseModel):
    endpoint: str


@router.get("/vapid-public-key")
async def get_vapid_public_key():
    """Return the VAPID public key for the browser to subscribe."""
    if not settings.push_enabled:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Push not configured")
    return {"publicKey": settings.vapid_public_key}


@router.post("/subscribe")
async def subscribe(body: SubscribeBody, ctx: Context = Depends(required_context)):
    """Save a push subscription for the current user."""
    if not settings.push_enabled:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Push not configured")
    await push_service.save_subscription(ctx[0].id, body.subscription)
    return {"ok": True}


@router.delete("/subscribe")
async def unsubscribe(body: UnsubscribeBody, ctx: Context = Depends(required_context)):
    """Remove a push subscription endpoint for the current user."""
    await push_service.delete_subscription(ctx[0].id, body.endpoint)
    return {"ok": True}
