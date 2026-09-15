"""Catalogue: styles, the home showcase and the daily Shots set.

The order is ours (manual curation, CH-14) and the personalisation is a lift on
top of it: directions the person marked ♥ during onboarding rise to the front,
everything else keeps the order we set. Both promises hold — we build the
showcase, and people see what they said they wanted first.
"""
from __future__ import annotations

from datetime import date
from typing import Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import models as m


async def list_styles(
    session: AsyncSession,
    *,
    category: Optional[str] = None,
    home_only: bool = False,
    limit: int = 50,
) -> Sequence[m.Style]:
    stmt = (
        select(m.Style)
        .where(m.Style.is_active.is_(True))
        .order_by(m.Style.sort_order, m.Style.title)
        .limit(limit)
    )
    if category:
        stmt = stmt.where(m.Style.category == category)
    if home_only:
        stmt = stmt.where(m.Style.is_home.is_(True))
    return (await session.scalars(stmt)).all()


def rank_for(styles: Sequence[m.Style], liked: Sequence[str]) -> list[m.Style]:
    """Manual order first, liked directions lifted to the top.

    A stable sort, so within a direction our curation survives untouched.
    """
    if not liked:
        return list(styles)
    liked_set = set(liked)
    return sorted(styles, key=lambda s: (0 if s.category in liked_set else 1,))


async def get(session: AsyncSession, style_id: str) -> Optional[m.Style]:
    style = await session.get(m.Style, style_id)
    if style is None or not style.is_active:
        return None
    return style


async def daily_shots(
    session: AsyncSession, day: date, *, count: Optional[int] = None
) -> Sequence[m.Style]:
    """The set for a given UTC day.

    Три слоя, и каждый отвечает за своё.

    Ручная замена на дату (`ShotsSchedule`) бьёт всё: если набор на день собран
    руками, он и показывается — в том порядке, в каком его записали.

    Дальше идёт закреплённая голова (`shots_pinned`). Первое, что человек видит
    на вкладке, выбираем мы, а не остаток от деления: девушка, она же в
    мультфильме, парень, он же в мультфильме — четыре карточки, по которым
    сразу читается, что здесь делают и на ком это работает. Вращать их значило
    бы менять первое впечатление о продукте каждые сутки.

    Хвост крутится как раньше: окно по пулу, сдвигаемое на шаг в день, без
    случайности, которую потом не воспроизвести. Все видят один и тот же
    «сегодня», и любую прошедшую дату можно повторить в тесте.
    """
    count = count or settings.shots_per_day
    override = await session.get(m.ShotsSchedule, day)
    if override is not None and override.style_ids:
        stmt = select(m.Style).where(m.Style.id.in_(override.style_ids))
        found = {row.id: row for row in await session.scalars(stmt)}
        # Порядок берём из самого расписания: список писали руками, и писали
        # его в том порядке, в каком хотели видеть.
        return [found[sid] for sid in override.style_ids if sid in found]

    stmt = (
        select(m.Style)
        .where(m.Style.is_active.is_(True), m.Style.is_shot.is_(True))
        .order_by(m.Style.id)
    )
    pool = list(await session.scalars(stmt))
    if not pool:
        return []

    by_id = {row.id: row for row in pool}
    head = [by_id[sid] for sid in settings.shots_pinned if sid in by_id]
    pinned = {row.id for row in head}
    tail = [row for row in pool if row.id not in pinned]
    if not tail:
        return head[:count]

    # Rotate the pool by the day number: a stable window that moves one step a
    # day, with no randomness to reproduce and no state to store.
    room = max(count - len(head), 0)
    offset = (day.toordinal() * count) % len(tail)
    doubled = tail + tail
    return head[:count] + doubled[offset : offset + min(room, len(tail))]
