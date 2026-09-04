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
# тарифу — квоту. ID товаров — как в App Store Connect (`week_6.99`, `year_39.99`,
# заведены до нас и не меняются); расхождение = покупка без квоты.
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
    ("weekly", "week_6.99", "Weekly", "week", 699, 300, 10),
    ("yearly", "year_39.99", "Pro Unlimited", "year", 3999, 500, 30),
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
#
# fal.ai заведён впереди OpenRouter — это и есть «вместо». Но выключать
# OpenRouter не нужно и не стоит: реестр умеет фолбэк, и отказ нового вендора
# тогда не роняет генерацию, а уводит её к проверенному.
#
# Пустой FAL_API_KEY при этом означает не отказ, а неучастие: `available()`
# вернёт False, реестр пройдёт мимо и даже не запишет это в причины. Поэтому
# строки можно завести заранее — они включатся сами, когда появится ключ.
#
# Две строки, а не одна: у fal текст-в-кадр и правка кадра — РАЗНЫЕ модели,
# и одной строкой они не покрываются.
PROVIDERS = [
    ("fal_nano", ["text_to_image"], "fal-ai/nano-banana", 0, True),
    ("fal_nano_edit", ["image_to_image"], "fal-ai/nano-banana/edit", 1, True),
    # gpt-image-2 через fal — та же модель OpenAI, что до сих пор жила только
    # на OpenRouter (229 кадров, самая массовая). Приоритет 1: запасной за
    # nano-banana, не основной. Три причины держать его вторым, а не первым:
    # ~35 с на кадр против ~12 у nano-banana; `medium` даёт 608×1088 (0,66 Мп)
    # против 768×1344 (1,03 Мп) у nano-banana; и модерация OpenAI отказывает на части референсов кодом 422 —
    # при том же промпте, что у соседей проходит. Отказ уходит дальше по
    # цепочке, как любой другой. Диалект входа — в адаптере (`_DIALECT`).
    ("fal_gpt2_edit", ["image_to_image"], "openai/gpt-image-2/edit", 0, True),
    ("openai_images", ["text_to_image"], "gpt-image-1", 10, True),
    ("pollinations", ["text_to_image"], "flux", 20, False),
]

# Сколько снимков берёт строка. Реестр отсеивает по этому числу раньше, чем
# запрос уйдёт: провайдер с одним референсом на парном кадре сделает вид, что
# справился, — вернёт одного человека и незнакомца рядом.
LIMITS = {
    "fal_nano_edit": {"max_references": 4},
    # По схеме fal: «A maximum of 16 images are allowed».
    "fal_gpt2_edit": {"max_references": 16},
}

# Прайс исполнителя — на случай, когда он не сообщает цену сам.
#
# OpenRouter кладёт `usage.cost` прямо в ответ, и у его работ в базе стоит
# ИЗМЕРЕННАЯ цена. fal её не отдаёт вовсе: только отдельный Usage API, к
# которому наш ключ не допущен. Без этой строки сводка себестоимости делила бы
# неполные доллары на полные TOONTOON и молча занижала расход — а по этому
# числу ставят цену подписки.
#
# Число взято из их же прайса (GET /v1/models/pricing), а не выдумано. Это
# по-прежнему ПРАЙС, а не факт: скидки сюда не входят, и часть отказов fal
# тарифицирует. Поэтому оно живёт в `cost_hint` («ориентир»), а не в
# `provider_cost_usd` («что стоило»), и сводка показывает, какая доля её числа
# взята из прайса.
COST_HINTS = {
    "fal_nano": {"usd_per_image": 0.0398},
    "fal_nano_edit": {"usd_per_image": 0.0398},
    # $0.042 — прайс gpt-image-1 medium 1024x1024, и `openai_images` ходит
    # именно в него. Кадров через него пока не проходило ни одного, так что
    # сверить с трафиком нечего.
    #
    # Ровно это число раньше лежало у `openrouter_gpt` и занижало его расход
    # на треть; убрано миграцией f2a6c19d84b3. Причина там оказалась не в
    # опечатке: одиночный замер — нижняя граница, а не цена. У всех трёх
    # моделей, что мы мерили, кадр гуляет в два-три раза, и лабораторный
    # замер ложится на дно разброса.
    #
    # Поэтому и здесь это ПОЛ, а не ожидание. Как только через openai_images
    # пойдёт живой трафик, сводка сама начнёт считать по медиане его замеров,
    # а число ниже останется опорой на случай молчания.
    "openai_images": {"usd_per_image": 0.042},
    # gpt-image-2 через fal, quality=medium, 864×1536 — ЗАМЕР, не прайс:
    # медиана по usage API за обкатку 2026-09-02 (три лица). Прайс OpenAI
    # давал $0.041 — сошлось. `high` — $0.1375 замером, втрое; не берём.
    "fal_gpt2_edit": {"usd_per_image": 0.0405},
}


async def seed_providers() -> None:
    async with session_scope() as session:
        for pid, operations, model, priority, enabled in PROVIDERS:
            limits = LIMITS.get(pid)
            hint = COST_HINTS.get(pid)
            stmt = insert(m.GenerationProvider).values(
                id=pid, operations=operations, model=model,
                priority=priority, is_enabled=enabled, limits=limits,
                cost_hint=hint,
            )
            # `is_enabled` намеренно НЕ перезаписывается: включённость — это
            # рычаг на живом сервисе, и пересев его при каждом запуске, мы
            # отменяли бы решение, принятое руками в три часа ночи.
            stmt = stmt.on_conflict_do_update(
                index_elements=[m.GenerationProvider.id],
                set_={"operations": operations, "model": model,
                      "priority": priority, "limits": limits, "cost_hint": hint},
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
