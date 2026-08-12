"""User repository.

A guest is an ordinary row with ``kind='guest'`` — that is the whole design.
Everything hanging off ``user_id`` (wallet, media, generations, chat) works for
them without a single branch, and signing in later becomes a merge rather than a
migration of half-built state.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import models as m


async def create_guest(session: AsyncSession) -> m.User:
    user = m.User(kind="guest", last_seen_at=func.now())
    session.add(user)
    await session.flush()
    return user


async def get(session: AsyncSession, user_id: str) -> Optional[m.User]:
    user = await session.get(m.User, user_id)
    if user is None or user.deleted_at is not None:
        return None
    return user


async def get_by_email(session: AsyncSession, email: str) -> Optional[m.User]:
    stmt = select(m.User).where(
        m.User.email == email.strip().lower(), m.User.deleted_at.is_(None)
    )
    return await session.scalar(stmt)


async def get_by_identity(
    session: AsyncSession, provider: str, external_id: str
) -> Optional[m.User]:
    stmt = (
        select(m.User)
        .join(m.AuthIdentity, m.AuthIdentity.user_id == m.User.id)
        .where(
            m.AuthIdentity.provider == provider,
            m.AuthIdentity.external_id == external_id,
            m.User.deleted_at.is_(None),
        )
    )
    return await session.scalar(stmt)


async def touch(session: AsyncSession, user_id: str) -> None:
    """Update last activity — abandoned-guest cleanup reads this."""
    await session.execute(
        update(m.User).where(m.User.id == user_id).values(last_seen_at=func.now())
    )


async def attach_identity(
    session: AsyncSession, user_id: str, provider: str, external_id: str
) -> m.AuthIdentity:
    identity = m.AuthIdentity(user_id=user_id, provider=provider, external_id=external_id)
    session.add(identity)
    await session.flush()
    return identity


async def merge_guest_into(
    session: AsyncSession, guest_id: str, target_id: str
) -> None:
    """Move a guest's work into an account on sign-in.

    Everything is reassigned rather than copied, and the guest row survives with
    ``merged_into_user_id`` set — if a merge is ever disputed, the trail is there.
    Wallet balances are deliberately NOT merged here: that rule belongs to the
    subscription model and is still open (CH-17).
    """
    if guest_id == target_id:
        return

    for model in (m.MediaAsset, m.Generation, m.ChatMessage):
        await session.execute(
            update(model).where(model.user_id == guest_id).values(user_id=target_id)
        )

    # Preferences move only if the account does not have its own answers yet —
    # the account's own onboarding is more recent and more true.
    existing = await session.get(m.UserPreferences, target_id)
    if existing is None:
        await session.execute(
            update(m.UserPreferences)
            .where(m.UserPreferences.user_id == guest_id)
            .values(user_id=target_id)
        )

    await session.execute(
        update(m.User)
        .where(m.User.id == guest_id)
        .values(merged_into_user_id=target_id, deleted_at=func.now())
    )
    await session.flush()
