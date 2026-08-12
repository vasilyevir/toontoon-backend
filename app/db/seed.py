"""Seed reference data: subscription plans.

Numbers come from the Glam AI teardown (docs/ECONOMY.md) and are a **starting
point, not a decision** — ours will differ once our own API costs are measured.
They live here so the wallet has something real to work against today.

Run: ``PYTHONPATH=. .venv/bin/python -m app.db.seed``
Idempotent: re-running updates existing rows instead of duplicating them.
"""
from __future__ import annotations

import asyncio

from sqlalchemy.dialects.postgresql import insert

from app.db import models as m
from app.db.session import connect, disconnect, session_scope

# id, product_id, title, billing_period, price_usd (cents), weekly_quota, sort
PLANS = [
    ("weekly", "ai.arteki.sub.weekly", "Безлимит", "week", 999, 850, 10),
    ("monthly", "ai.arteki.sub.monthly", "Pro Unlimited", "month", 1499, 1500, 20),
    ("yearly", "ai.arteki.sub.yearly", "Pro Unlimited", "year", 4999, 1000, 30),
    ("business_yearly", "ai.arteki.sub.business.yearly", "Бизнес Безлимит", "year", 9900, 5000, 40),
]


async def seed_plans() -> None:
    async with session_scope() as session:
        for plan_id, product_id, title, period, price, quota, order in PLANS:
            stmt = insert(m.SubscriptionPlan).values(
                id=plan_id,
                product_id=product_id,
                title=title,
                billing_period=period,
                price_usd=price,
                weekly_quota=quota,
                sort_order=order,
                is_active=True,
            )
            # Re-seeding is how prices get updated, so overwrite on conflict.
            stmt = stmt.on_conflict_do_update(
                index_elements=[m.SubscriptionPlan.id],
                set_={
                    "product_id": product_id,
                    "title": title,
                    "billing_period": period,
                    "price_usd": price,
                    "weekly_quota": quota,
                    "sort_order": order,
                    "is_active": True,
                },
            )
            await session.execute(stmt)


async def main() -> None:
    await connect()
    await seed_plans()
    await disconnect()
    print(f"Тарифов записано: {len(PLANS)}")


if __name__ == "__main__":
    asyncio.run(main())
