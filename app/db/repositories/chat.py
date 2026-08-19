"""Chat repository — one continuous thread per user (CH-20).

There is no ``chats`` table and no thread list: finding an old work is what the
history tab is for, and keeping two mechanisms for the same job means keeping
two answers to the same question.

"Clear" does not delete anything. It moves a marker on the user, so the model
forgets the previous conversation while the person can still scroll back to it.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional, Sequence

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import models as m


async def add_message(
    session: AsyncSession,
    *,
    user_id: str,
    role: str,
    content: Optional[str] = None,
    generation_id: Optional[str] = None,
    media_id: Optional[str] = None,
) -> m.ChatMessage:
    message = m.ChatMessage(
        user_id=user_id, role=role, content=content,
        generation_id=generation_id, media_id=media_id,
    )
    session.add(message)
    await session.flush()
    return message


async def list_messages(
    session: AsyncSession,
    user: m.User,
    *,
    limit: int = 30,
    before: Optional[datetime] = None,
) -> Sequence[m.ChatMessage]:
    """A page of the thread, newest first.

    The single thread grows without bound, so loading it whole is not an
    option — that is the price of dropping thread lists, and it is paid here.

    «Очистка» здесь настоящая: всё, что было до неё, не показывается. Раньше
    экран продолжал показывать старое, а модель его уже не помнила — человек
    видел свою переписку и разговаривал с собеседником, который её забыл. Сами
    строки остаются в базе: они привязаны к работам и к списаниям, и удалять их
    ради вида нельзя.
    """
    stmt = (
        select(m.ChatMessage)
        .where(m.ChatMessage.user_id == user.id)
        .order_by(m.ChatMessage.created_at.desc(), m.ChatMessage.id.desc())
        .limit(limit)
    )
    if user.chat_context_started_at is not None:
        stmt = stmt.where(m.ChatMessage.created_at >= user.chat_context_started_at)
    if before is not None:
        stmt = stmt.where(m.ChatMessage.created_at < before)
    return (await session.scalars(stmt)).all()


async def context_messages(
    session: AsyncSession, user: m.User, *, limit: int
) -> list[dict]:
    """What the model gets: the last N messages since the most recent Clear.

    Capped on purpose — the cost of a request must not grow with the age of the
    conversation, or an active person's chat becomes more expensive every day.
    """
    stmt = (
        select(m.ChatMessage)
        .where(m.ChatMessage.user_id == user.id)
        .order_by(m.ChatMessage.created_at.desc(), m.ChatMessage.id.desc())
        .limit(limit)
    )
    if user.chat_context_started_at is not None:
        stmt = stmt.where(m.ChatMessage.created_at >= user.chat_context_started_at)
    rows = list(await session.scalars(stmt))
    rows.reverse()
    return [
        {"role": r.role, "content": r.content or ""}
        for r in rows
        if r.content
    ]


async def clear_context(session: AsyncSession, user_id: str) -> None:
    """Start a new conversation without losing the old one.

    Takes an id, not the user object: the row that ``deps`` hands routers was
    loaded in a different session and is detached, so assigning to it writes
    nothing. This bug is silent — Clear would appear to work while the model
    kept remembering — so the update is issued explicitly.
    """
    await session.execute(
        update(m.User).where(m.User.id == user_id).values(chat_context_started_at=func.now())
    )
    await session.flush()
