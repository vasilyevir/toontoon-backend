"""Generation records + public share links in Redis.

Key layout
----------
* ``generation:{id}``          -> Generation JSON
* ``user:generations:{uid}``   -> Redis list of generation ids (newest first)
* ``share:{share_id}``         -> generation id (public, no auth)
"""
from __future__ import annotations

from typing import Optional

from app.core.security import new_id
from app.models.generation import Generation
from app.redis_client import get_client

_MAX_HISTORY = 200


def _gen_key(gen_id: str) -> str:
    return f"generation:{gen_id}"


def _user_list_key(user_id: str) -> str:
    return f"user:generations:{user_id}"


def _share_key(share_id: str) -> str:
    return f"share:{share_id}"


async def save(generation: Generation) -> None:
    redis = get_client()
    await redis.set(_gen_key(generation.id), generation.model_dump_json())


async def add_for_user(generation: Generation) -> None:
    """Persist a generation and index it at the head of the user's history."""
    redis = get_client()
    await save(generation)
    list_key = _user_list_key(generation.user_id)
    await redis.lpush(list_key, generation.id)
    await redis.ltrim(list_key, 0, _MAX_HISTORY - 1)


async def get(gen_id: str) -> Optional[Generation]:
    redis = get_client()
    raw = await redis.get(_gen_key(gen_id))
    return Generation.model_validate_json(raw) if raw else None


async def list_for_user(user_id: str) -> list[Generation]:
    redis = get_client()
    ids = await redis.lrange(_user_list_key(user_id), 0, _MAX_HISTORY - 1)
    if not ids:
        return []
    raws = await redis.mget(*[_gen_key(gid) for gid in ids])
    return [Generation.model_validate_json(raw) for raw in raws if raw]


async def create_share(generation: Generation) -> str:
    """Create (or reuse) a public share id for a generation."""
    redis = get_client()
    if generation.share_id:
        return generation.share_id
    share_id = new_id()
    generation.share_id = share_id
    await save(generation)
    await redis.set(_share_key(share_id), generation.id)
    return share_id


async def get_by_share(share_id: str) -> Optional[Generation]:
    redis = get_client()
    gen_id = await redis.get(_share_key(share_id))
    return await get(gen_id) if gen_id else None
