"""Сессии и одноразовые токены в Redis.

Личность живёт в PostgreSQL (`app/db/repositories/users.py`), здесь остаётся
только то, у чего есть срок годности, — Redis для этого и нужен.

Раскладка ключей
----------------
* ``session:{sid}``   -> Session JSON  (TTL = SESSION_TTL_DAYS)
* ``magic:{token}``   -> email         (TTL = MAGIC_LINK_TTL_MINUTES)
* ``reset:{token}``   -> email         (TTL = PASSWORD_RESET_TTL_MINUTES)

Здесь же лежал ВТОРОЙ склад личностей: `user:{id}`, `user:email:{...}`,
`user:google:{sub}` и девять функций вокруг них, включая заведение
пользователей с начальным балансом. После переезда на PostgreSQL его не звал
никто — ни одной строки во всём коде. Удалён: мёртвый код аутентификации
опаснее обычного мёртвого кода, потому что выглядит рабочим и однажды его
позовут обратно.
"""
from __future__ import annotations

import time
from typing import Optional

from app.config import settings
from app.core.security import new_token
from app.models.session import Session
from app.models.user import AuthProvider
from app.redis_client import get_client


def _session_key(sid: str) -> str:
    return f"session:{sid}"


def _magic_key(token: str) -> str:
    return f"magic:{token}"


# ─── Magic-link tokens ──────────────────────────────────────────────────────


async def create_magic_token(email: str) -> str:
    redis = get_client()
    token = new_token()
    await redis.set(_magic_key(token), email, ex=settings.magic_link_ttl_seconds)
    return token


async def consume_magic_token(token: str) -> Optional[str]:
    """Return the email for a valid token and delete it (single use)."""
    redis = get_client()
    email = await redis.get(_magic_key(token))
    if email:
        await redis.delete(_magic_key(token))
    return email


# ─── Password-reset tokens ────────────────────────────────────────────────────


def _reset_key(token: str) -> str:
    return f"reset:{token}"


async def create_reset_token(email: str) -> str:
    redis = get_client()
    token = new_token()
    await redis.set(_reset_key(token), email, ex=settings.password_reset_ttl_seconds)
    return token


async def consume_reset_token(token: str) -> Optional[str]:
    """Return the email for a valid reset token and delete it (single use)."""
    redis = get_client()
    email = await redis.get(_reset_key(token))
    if email:
        await redis.delete(_reset_key(token))
    return email


# ─── Sessions ─────────────────────────────────────────────────────────────────


async def create_session_for_user_id(user_id: str, provider: AuthProvider) -> Session:
    """Session for a user that lives in PostgreSQL rather than Redis.

    Sessions stay in Redis either way — short-lived, TTL-driven, exactly what it
    is good at. Only the identity moved.
    """
    redis = get_client()
    session = Session(sid=new_token(), user_id=user_id, provider=provider,
                      issued_at=time.time())
    await redis.set(
        _session_key(session.sid), session.model_dump_json(), ex=settings.session_ttl_seconds
    )
    return session


async def get_session(sid: str) -> Optional[Session]:
    redis = get_client()
    raw = await redis.get(_session_key(sid))
    return Session.model_validate_json(raw) if raw else None


async def update_session(session: Session) -> None:
    redis = get_client()
    await redis.set(_session_key(session.sid), session.model_dump_json(), ex=settings.session_ttl_seconds)


async def delete_session(sid: str) -> None:
    redis = get_client()
    await redis.delete(_session_key(sid))
