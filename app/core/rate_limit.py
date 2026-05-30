"""Fixed-window rate limiting backed by Redis."""
from __future__ import annotations

from app.redis_client import get_client


async def hit(key: str, limit: int, window_seconds: int) -> tuple[bool, int]:
    """Register one hit against ``key``.

    Returns ``(allowed, remaining)``. The window starts on the first hit and
    expires after ``window_seconds``.
    """
    redis = get_client()
    redis_key = f"ratelimit:{key}"
    current = await redis.incr(redis_key)
    if current == 1:
        await redis.expire(redis_key, window_seconds)
    allowed = current <= limit
    remaining = max(0, limit - current)
    return allowed, remaining
