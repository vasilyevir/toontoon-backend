"""Seed reference data: subscription plans.

Numbers come from the Glam AI teardown (docs/ECONOMY.md) and are a **starting
point, not a decision** — ours will differ once our own API costs are measured.
They live here so the wallet has something real to work against today.

Run: ``PYTHONPATH=. .venv/bin/python -m app.db.seed``
Idempotent: re-running updates existing rows instead of duplicating them.
"""
from __future__ import annotations

import asyncio

from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert

from app.db import models as m
from app.db.session import connect, disconnect, session_scope

# id, product_id, title, billing_period, price_usd (cents), weekly_quota, sort
# Идентификаторы товаров обязаны совпадать с App Store Connect и с
# `Toontoon.storekit`: по productId из чека подписка находит свой тариф, а по
# тарифу — квоту. Здесь стояло `ai.toontoon.sub.*`, а товары называются
# `ai.toontoon.ios.*` — покупка не нашла бы тарифа и осталась бы без квоты.
#
# Квоты пересчитаны от нашей себестоимости 27 августа. Шкала: фото 10 монет,
# рисунок 20, разговор 15.
#
#   Недельный 300 — прибылен даже если выбрать всё до последней монеты:
#   тридцать фотографий стоят нам $2.25 против $8.49 выручки.
#
#   Годовой 500 — сознательная ставка. При полном расходовании убыточен
#   (−$2.93 в неделю); выходит в ноль, если подписчики в среднем тратят
#   не больше 22% квоты, то есть одиннадцать фото в неделю. Ставка на то, что
#   годовой берут и забывают; у нас нет ни одного наблюдения, чтобы её
#   подтвердить, — поэтому названа вслух.
PLANS = [
    # Названия видны на экране подписки, а интерфейс английский.
    #
    # Месячный и бизнесовый убраны: таких товаров нет ни в App Store Connect,
    # ни в локальной конфигурации, и купить их нельзя. Тариф, который отдаётся
    # в каталоге, но не покупается, — обещание без исполнения.
    ("weekly", "ai.toontoon.ios.weekly", "Weekly", "week", 999, 300, 10),
    ("yearly", "ai.toontoon.ios.yearly", "Pro Unlimited", "year", 4999, 500, 30),
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

        # Не заявленное здесь — гасим. Сид описывает витрину целиком, а не
        # добавляет к ней: убранный тариф иначе остаётся активным навсегда и
        # продолжает отдаваться в каталоге. Именно так пережили переименование
        # месячный и бизнесовый — с товарами `ai.teki.*`, которых нет ни в App
        # Store, ни в локальной конфигурации. Купить их нельзя, а предложить
        # можно: обещание без исполнения.
        #
        # Строки не удаляются: у тарифа может быть история подписок, и потерять
        # её значит потерять ответ на вопрос «по какому тарифу человек платил».
        await session.execute(
            update(m.SubscriptionPlan)
            .where(m.SubscriptionPlan.id.notin_([p[0] for p in PLANS]))
            .values(is_active=False)
        )


# id, операции, модель, приоритет, включён
# image_to_image у OpenAI ВЫКЛЮЧЕН: адаптер написан, но включать его можно
# только после проверки на реальных лицах — вопрос не в том, отвечает ли API,
# а в том, узнаётся ли человек (CH-19).
# Pollinations убран из запасных 26 августа 2026 по решению лида.
#
# Запасной исполнитель, который рисует плохо, — хуже отсутствия запасного:
# человек платит TOONTOON и получает кадр, которого не просил, а мы об этом не
# узнаём — работа же «удалась». Отказ честнее: за него возвращаются деньги, и
# он виден в журнале.
#
# Строка в базе не удаляется, а выключается: у неё есть история генераций,
# и удалить её значило бы потерять ответ на вопрос «чем сделан вот этот кадр».
PROVIDERS = [
    ("openai_images", ["text_to_image"], "gpt-image-1", 10, True),
    ("pollinations", ["text_to_image"], "flux", 20, False),
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
