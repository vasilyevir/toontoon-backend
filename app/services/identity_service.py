"""Identity on PostgreSQL: find or create the user behind a sign-in.

Replaces the Redis lookups in ``auth_service`` for everything except sessions,
which stay in Redis where TTLs belong. Sign-in providers are rows in
``auth_identities``, so one account can carry Apple, Google and a password at
once instead of becoming three accounts.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import models as m
from app.db.repositories import users as users_repo
from app.db.repositories import wallet as wallet_repo


async def _grant_signup_bonus(db: AsyncSession, user_id: str) -> None:
    """One-time welcome balance. The idempotency key is what makes "once" true."""
    if settings.signup_teki_balance <= 0:
        return
    await wallet_repo.grant(
        db,
        user_id,
        amount=settings.signup_teki_balance,
        bucket="free",
        reason="signup",
        idempotency_key=f"signup:{user_id}",
    )


async def create_email_user(
    db: AsyncSession, *, email: str, password_hash: str, name: str
) -> m.User:
    user = m.User(
        kind="registered",
        email=email.strip().lower(),
        password_hash=password_hash,
        name=name,
    )
    db.add(user)
    await db.flush()
    await users_repo.attach_identity(db, user.id, "email", user.email)
    await _grant_signup_bonus(db, user.id)
    return user


async def get_or_create_oauth_user(
    db: AsyncSession,
    *,
    provider: str,
    external_id: str,
    email: Optional[str] = None,
    name: Optional[str] = None,
) -> m.User:
    """Apple, Google and magic-link all land here.

    If the email already belongs to an account, the new provider is attached to
    it rather than starting a second account — signing in with Google after
    having registered by password must not split a person in two.
    """
    existing = await users_repo.get_by_identity(db, provider, external_id)
    if existing is not None:
        return existing

    user: Optional[m.User] = None
    if email:
        user = await users_repo.get_by_email(db, email)

    if user is None:
        fallback = (email or "").split("@", 1)[0] or "Arteki user"
        user = m.User(
            kind="registered",
            email=email.strip().lower() if email else None,
            name=name or fallback,
        )
        db.add(user)
        await db.flush()
        await _grant_signup_bonus(db, user.id)
    elif name and not user.name:
        user.name = name

    await users_repo.attach_identity(db, user.id, provider, external_id)
    await db.flush()
    return user


async def promote_guest(
    db: AsyncSession, *, guest_id: str, target: m.User
) -> None:
    """Fold a guest's work into the account they just signed into (CH-16)."""
    await users_repo.merge_guest_into(db, guest_id, target.id)
