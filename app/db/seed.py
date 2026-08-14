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
    # Названия видны на экране подписки, а интерфейс английский: три из
    # четырёх были русскими и на экране смотрелись бы как чужой текст.
    ("weekly", "ai.toontoon.sub.weekly", "Weekly", "week", 999, 850, 10),
    ("monthly", "ai.toontoon.sub.monthly", "Pro Unlimited", "month", 1499, 1500, 20),
    ("yearly", "ai.toontoon.sub.yearly", "Pro Unlimited", "year", 4999, 1000, 30),
    ("business_yearly", "ai.toontoon.sub.business.yearly", "Business Unlimited", "year", 9900, 5000, 40),
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


# id, операции, модель, приоритет, включён
# image_to_image у OpenAI ВЫКЛЮЧЕН: адаптер написан, но включать его можно
# только после проверки на реальных лицах — вопрос не в том, отвечает ли API,
# а в том, узнаётся ли человек (CH-19).
PROVIDERS = [
    ("openai_images", ["text_to_image"], "gpt-image-1", 10, True),
    # Kling — основной кандидат на «твоё фото в этом стиле». Обе строки
    # выключены: сперва прогон на портретах, потом прод.
    #   kling     — третья версия, основной поток с референсами.
    #   kling_v2  — вторая, про запас: та же схема запроса, другое качество
    #               и другая цена.
    ("kling", ["text_to_image", "image_to_image"], "kling-v3", 5, False),
    ("kling_v2", ["text_to_image", "image_to_image"], "kling-v2-1", 6, False),
    ("pollinations", ["text_to_image"], "flux", 20, True),
]


async def seed_providers() -> None:
    async with session_scope() as session:
        for pid, operations, model, priority, enabled in PROVIDERS:
            stmt = insert(m.GenerationProvider).values(
                id=pid, operations=operations, model=model,
                priority=priority, is_enabled=enabled,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=[m.GenerationProvider.id],
                set_={"operations": operations, "model": model, "priority": priority},
            )
            await session.execute(stmt)


async def main() -> None:
    await connect()
    await seed_plans()
    await seed_providers()
    await disconnect()
    print(f"Тарифов записано: {len(PLANS)}, провайдеров: {len(PROVIDERS)}")


if __name__ == "__main__":
    asyncio.run(main())
