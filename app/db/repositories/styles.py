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
    session: AsyncSession, day: date, *, count: int = 6
) -> Sequence[m.Style]:
    """The set for a given UTC day.

    A manual override wins if there is one; otherwise the set is computed from
    the pool deterministically, so everyone sees the same "today" and a test can
    reproduce any date. Rotation runs by UTC on purpose — a local date would
    give different people different todays and turn debugging into guesswork.
    """
    override = await session.get(m.ShotsSchedule, day)
    if override is not None and override.style_ids:
        stmt = select(m.Style).where(m.Style.id.in_(override.style_ids))
        return (await session.scalars(stmt)).all()

    stmt = (
        select(m.Style)
        .where(m.Style.is_active.is_(True), m.Style.is_shot.is_(True))
        .order_by(m.Style.id)
    )
    pool = list(await session.scalars(stmt))
    if not pool:
        return []

    # Rotate the pool by the day number: a stable window that moves one step a
    # day, with no randomness to reproduce and no state to store.
    offset = (day.toordinal() * count) % len(pool)
    doubled = pool + pool
    return doubled[offset : offset + min(count, len(pool))]
