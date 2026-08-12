"""Generation repository.

Replaces the Redis list that was capped at 200 entries — history is kept for
good now (CH-13), which is only possible with pagination and an index instead
of "load everything the user ever made".
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import new_id
from app.db import models as m


async def create(
    session: AsyncSession,
    *,
    user_id: str,
    operation: str,
    status: str = "queued",
    style_id: Optional[str] = None,
    prompt: Optional[str] = None,
    request_params: Optional[dict] = None,
    source_media_id: Optional[str] = None,
    cost: int = 0,
) -> m.Generation:
    generation = m.Generation(
        user_id=user_id,
        operation=operation,
        status=status,
        style_id=style_id,
        prompt=prompt,
        # Without the request there is nothing to repeat from history: the
        # result alone cannot be turned back into the question that produced it.
        request_params=request_params or {},
        source_media_id=source_media_id,
        cost=cost,
    )
    session.add(generation)
    await session.flush()
    return generation


async def get(
    session: AsyncSession, generation_id: str, *, user_id: Optional[str] = None
) -> Optional[m.Generation]:
    generation = await session.get(m.Generation, generation_id)
    if generation is None or generation.deleted_at is not None:
        return None
    if user_id is not None and generation.user_id != user_id:
        return None
    return generation


async def list_for_user(
    session: AsyncSession,
    user_id: str,
    *,
    limit: int = 30,
    before: Optional[datetime] = None,
    operation: Optional[str] = None,
) -> Sequence[m.Generation]:
    """Newest first, one page at a time.

    Paged by creation time rather than by offset: the list grows at the head,
    and an offset would quietly skip or repeat items as it does.
    """
    stmt = (
        select(m.Generation)
        .where(m.Generation.user_id == user_id, m.Generation.deleted_at.is_(None))
        .order_by(m.Generation.created_at.desc())
        .limit(limit)
    )
    if before is not None:
        stmt = stmt.where(m.Generation.created_at < before)
    if operation is not None:
        stmt = stmt.where(m.Generation.operation == operation)
    return (await session.scalars(stmt)).all()


async def mark_done(
    session: AsyncSession, generation: m.Generation, *, result_media_id: str, prompt: Optional[str] = None
) -> m.Generation:
    generation.status = "done"
    generation.result_media_id = result_media_id
    generation.finished_at = func.now()
    if prompt is not None:
        generation.prompt = prompt
    await session.flush()
    return generation


async def mark_failed(
    session: AsyncSession, generation: m.Generation, *, error: str
) -> m.Generation:
    """A failed attempt stays in the table.

    The alternative — leaving no trace — makes "I was charged and got nothing"
    impossible to investigate, even when the charge was correctly rolled back.
    """
    generation.status = "failed"
    generation.error = error[:2000]
    generation.finished_at = func.now()
    await session.flush()
    return generation


async def soft_delete(session: AsyncSession, generation: m.Generation) -> None:
    """Hide the work and kill its share link.

    The row survives because the ledger points at it; erasing the bytes is the
    caller's job (media repository), which is also what makes "deleted" true.
    """
    generation.deleted_at = func.now()
    generation.share_id = None
    await session.flush()


async def ensure_share_id(session: AsyncSession, generation: m.Generation) -> str:
    if not generation.share_id:
        generation.share_id = new_id("shr_")
        await session.flush()
    return generation.share_id


async def get_by_share_id(session: AsyncSession, share_id: str) -> Optional[m.Generation]:
    stmt = select(m.Generation).where(
        m.Generation.share_id == share_id, m.Generation.deleted_at.is_(None)
    )
    return await session.scalar(stmt)
