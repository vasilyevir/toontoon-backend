"""Redis connection management.

Exposes a single async client for the whole app. When ``USE_FAKE_REDIS`` is
enabled we fall back to an in-memory implementation so the API can run locally
without a Redis server (useful for tests / quick demos).
"""
from __future__ import annotations

from typing import Optional

import redis.asyncio as aioredis

from app.config import settings

_client: Optional["aioredis.Redis"] = None


def _build_client() -> "aioredis.Redis":
    if settings.use_fake_redis:
        try:
            import fakeredis.aioredis as fakeredis  # type: ignore
        except ImportError as exc:  # pragma: no cover - guard rail
            raise RuntimeError(
                "USE_FAKE_REDIS=true but the 'fakeredis' package is not installed. "
                "Run `pip install fakeredis` or set USE_FAKE_REDIS=false."
            ) from exc
        return fakeredis.FakeRedis(decode_responses=True)

    return aioredis.from_url(settings.redis_url, decode_responses=True)


async def connect() -> "aioredis.Redis":
    global _client
    if _client is None:
        _client = _build_client()
        # Fail fast if the server is unreachable.
        await _client.ping()
    return _client


async def disconnect() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def get_client() -> "aioredis.Redis":
    if _client is None:
        raise RuntimeError("Redis client is not initialised. Did the app lifespan run?")
    return _client
