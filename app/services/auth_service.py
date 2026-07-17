"""User, session and magic-link persistence in Redis.

Key layout
----------
* ``user:{id}``                -> User JSON
* ``user:email:{email}``       -> user id   (magic-link lookup)
* ``session:{sid}``            -> Session JSON  (TTL = SESSION_TTL_DAYS)
* ``magic:{token}``            -> email       (TTL = MAGIC_LINK_TTL_MINUTES)
* ``user:generations:{id}``    -> list of generation ids (managed elsewhere)
"""
from __future__ import annotations

from typing import Optional

from app.config import settings
from app.core.security import new_id, new_token
from app.models.session import Session
from app.models.user import AuthProvider, User
from app.redis_client import get_client


def _user_key(user_id: str) -> str:
    return f"user:{user_id}"


def _email_key(email: str) -> str:
    return f"user:email:{email.lower()}"


def _session_key(sid: str) -> str:
    return f"session:{sid}"


def _magic_key(token: str) -> str:
    return f"magic:{token}"


# ─── Users ────────────────────────────────────────────────────────────────────


async def save_user(user: User) -> None:
    redis = get_client()
    await redis.set(_user_key(user.id), user.model_dump_json())
    if user.email:
        await redis.set(_email_key(user.email), user.id)


async def get_user(user_id: str) -> Optional[User]:
    redis = get_client()
    raw = await redis.get(_user_key(user_id))
    return User.model_validate_json(raw) if raw else None


async def delete_user(user_id: str) -> None:
    redis = get_client()
    user = await get_user(user_id)
    if user and user.email:
        await redis.delete(_email_key(user.email))
    await redis.delete(_user_key(user_id))


async def get_or_create_magic_user(email: str) -> User:
    """Find a magic-link user by email or create one with the signup balance."""
    redis = get_client()
    existing_id = await redis.get(_email_key(email))
    if existing_id:
        user = await get_user(existing_id)
        if user:
            return user

    user = User(
        id=new_id("usr_"),
        provider=AuthProvider.MAGIC,
        email=email,
        name=email.split("@", 1)[0],
        teki_balance=settings.signup_teki_balance,
    )
    await save_user(user)
    return user


async def get_user_by_email(email: str) -> Optional[User]:
    """Look up a user by (case-insensitive) email, if any."""
    redis = get_client()
    existing_id = await redis.get(_email_key(email))
    if not existing_id:
        return None
    return await get_user(existing_id)


async def create_email_user(email: str, password_hash: str, name: str) -> User:
    """Create a new email+password user with the signup TEKI balance."""
    user = User(
        id=new_id("usr_"),
        provider=AuthProvider.EMAIL,
        email=email,
        name=name,
        password_hash=password_hash,
        teki_balance=settings.signup_teki_balance,
    )
    await save_user(user)
    return user


async def get_or_create_boostify_user(info: dict) -> User:
    """Find/create a user from a Boostify userinfo payload."""
    boostify_id = info["sub"]
    redis = get_client()
    existing_id = await redis.get(f"user:boostify:{boostify_id}")
    if existing_id:
        user = await get_user(existing_id)
        if user:
            return user

    user = User(
        id=new_id("usr_"),
        provider=AuthProvider.BOOSTIFY,
        email=info.get("email"),
        name=info.get("name"),
        avatar=info.get("avatar"),
        boostify_user_id=boostify_id,
    )
    await save_user(user)
    await redis.set(f"user:boostify:{boostify_id}", user.id)
    return user


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


async def create_session(
    user: User,
    *,
    boostify_access_token: Optional[str] = None,
    boostify_refresh_token: Optional[str] = None,
    boostify_access_expires_at: Optional[float] = None,
) -> Session:
    redis = get_client()
    session = Session(
        sid=new_token(),
        user_id=user.id,
        provider=user.provider,
        boostify_access_token=boostify_access_token,
        boostify_refresh_token=boostify_refresh_token,
        boostify_access_expires_at=boostify_access_expires_at,
    )
    await redis.set(_session_key(session.sid), session.model_dump_json(), ex=settings.session_ttl_seconds)
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
