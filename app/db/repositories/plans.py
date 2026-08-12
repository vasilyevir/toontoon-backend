"""Subscription plans.

Tariffs live in the database because pricing changes on a product schedule, not
an engineering one — and every change should not cost an app release.
"""
from __future__ import annotations

from typing import Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import models as m


async def list_active(session: AsyncSession) -> Sequence[m.SubscriptionPlan]:
    stmt = (
        select(m.SubscriptionPlan)
        .where(m.SubscriptionPlan.is_active.is_(True))
        .order_by(m.SubscriptionPlan.sort_order)
    )
    return (await session.scalars(stmt)).all()


async def get(session: AsyncSession, plan_id: str) -> Optional[m.SubscriptionPlan]:
    return await session.get(m.SubscriptionPlan, plan_id)


async def get_by_product(session: AsyncSession, product_id: str) -> Optional[m.SubscriptionPlan]:
    stmt = select(m.SubscriptionPlan).where(m.SubscriptionPlan.product_id == product_id)
    return await session.scalar(stmt)
