"""Web Push notification service (VAPID / pywebpush).

Subscriptions are stored in Redis: `push_subs:{user_id}` → JSON list.
Each subscription is the standard PushSubscription JSON object from the browser:
  { "endpoint": "...", "keys": { "p256dh": "...", "auth": "..." } }
"""
from __future__ import annotations

import json
import logging

from app.config import settings

logger = logging.getLogger("toontoon.push")

_REDIS_PREFIX = "push_subs:"
_MAX_SUBS_PER_USER = 10


def _get_redis():
    from app.redis_client import get_client

    return get_client()


async def save_subscription(user_id: str, sub: dict) -> None:
    """Persist a push subscription for a user (deduplicates by endpoint)."""
    r = _get_redis()
    key = _REDIS_PREFIX + user_id
    raw = await r.get(key)
    subs = json.loads(raw) if raw else []
    endpoint = sub.get("endpoint", "")
    subs = [s for s in subs if s.get("endpoint") != endpoint]
    subs.append(sub)
    subs = subs[-_MAX_SUBS_PER_USER:]
    await r.set(key, json.dumps(subs))
    logger.info("Saved push sub for user=%s endpoint=...%s", user_id, endpoint[-12:])


async def delete_subscription(user_id: str, endpoint: str) -> None:
    """Remove a specific push subscription endpoint."""
    r = _get_redis()
    key = _REDIS_PREFIX + user_id
    raw = await r.get(key)
    if not raw:
        return
    subs = json.loads(raw)
    subs = [s for s in subs if s.get("endpoint") != endpoint]
    await r.set(key, json.dumps(subs))


async def get_subscriptions(user_id: str) -> list[dict]:
    """Return all active push subscriptions for a user."""
    r = _get_redis()
    raw = await r.get(_REDIS_PREFIX + user_id)
    return json.loads(raw) if raw else []


async def send_push_to_user(
    user_id: str,
    title: str,
    body: str,
    url: str = "/generate",
    icon: str = "/assets/icon-192.png",
) -> None:
    """Send a Web Push notification to all subscriptions of a user."""
    if not settings.push_enabled:
        logger.debug("Push disabled (no VAPID keys configured)")
        return

    subs = await get_subscriptions(user_id)
    if not subs:
        logger.debug("No push subscriptions for user=%s", user_id)
        return

    import pywebpush

    payload = json.dumps({"title": title, "body": body, "url": url, "icon": icon})
    stale: list[str] = []

    for sub in subs:
        try:
            pywebpush.webpush(
                subscription_info=sub,
                data=payload,
                vapid_private_key=settings.vapid_private_key,
                vapid_claims={"sub": settings.vapid_email},
            )
            logger.info("Push sent to user=%s endpoint=...%s", user_id, sub.get("endpoint", "")[-12:])
        except pywebpush.WebPushException as exc:
            response = getattr(exc, "response", None)
            status_code = getattr(response, "status_code", None)
            logger.warning("Push failed (status=%s): %s", status_code, exc)
            if status_code in (404, 410):
                stale.append(sub.get("endpoint", ""))
        except Exception as exc:
            logger.warning("Push error: %r", exc)

    for endpoint in stale:
        await delete_subscription(user_id, endpoint)
