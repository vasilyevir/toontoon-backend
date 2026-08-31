"""Порядок разделов витрины — один для всех.

Он выставлен вручную в `CATEGORIES` и рассчитан на первое впечатление:
сильное сверху, редкое внизу. Рядом лежит персонализация — направления,
отмеченные ♥ в онбординге, всплывают наверх, — и она этот расчёт
переворачивала: человек, отметивший шесть направлений из одиннадцати, видел
первыми их, а выстроенный порядок начинал работать с седьмой ленты.

Проверять это глазами дорого и ненадёжно. Один раз уже вышло так: порядок на
сервере переставили, на экране не изменилось ничего, и полдня ушло на поиски
кэша, которого не было. Виноваты были двое сразу — свой список порядка в
приложении и эта самая персонализация.

Отсюда тест: каталог спрашивают от лица человека, которому нравится ровно
хвост списка, и требуют, чтобы порядок не шелохнулся.
"""
from __future__ import annotations

import pytest_asyncio
from sqlalchemy import delete

from app.config import settings
from app.db.session import connect, disconnect, get_factory
from app.db import models as m
from app.models.session import Session
from app.routers import styles as styles_router
from app.routers.onboarding import CATEGORIES


@pytest_asyncio.fixture
async def человек_со_вкусами():
    """Тот, кому нравится хвост списка: если персонализация жива, она это покажет."""
    await connect()
    async with get_factory()() as session:
        user = m.User(kind="guest")
        session.add(user)
        await session.flush()
        session.add(m.UserPreferences(
            user_id=user.id,
            liked_categories=[CATEGORIES[-1], CATEGORIES[-2], CATEGORIES[-3]],
        ))
        await session.flush()
        # Сессия живёт в Redis, а не в базе: собираем её объектом, как это
        # делает `optional_context`.
        сессия = Session(sid=f"sid_{user.id[-8:]}", user_id=user.id, provider="guest")
        yield session, (user, сессия)
        await session.execute(delete(m.UserPreferences).where(m.UserPreferences.user_id == user.id))
        await session.execute(delete(m.User).where(m.User.id == user.id))
        await session.commit()
    await disconnect()


async def test_catalogue_order_ignores_what_the_person_liked(человек_со_вкусами):
    session, ctx = человек_со_вкусами
    разделы = await styles_router.list_styles(ctx=ctx, db=session)
    ожидаем = [c for c in CATEGORIES if c not in settings.hidden_category_list]
    assert [р.id for р in разделы] == ожидаем, (
        "порядок разделов уехал: он должен быть тем, что в CATEGORIES, "
        "минус скрытые"
    )


async def test_hidden_categories_do_not_show(человек_со_вкусами):
    """Скрытый раздел не показывается вовсе — ни лентой, ни пустым заголовком.

    Отдельным тестом, потому что первый сравнивает с тем же списком, из
    которого скрытые уже вычтены: если фильтр перестанет работать, он это
    заметит, а вот если скрытых станет ноль — промолчит.
    """
    session, ctx = человек_со_вкусами
    if not settings.hidden_category_list:
        return
    разделы = {р.id for р in await styles_router.list_styles(ctx=ctx, db=session)}
    спрятанные = разделы & set(settings.hidden_category_list)
    assert not спрятанные, f"скрытые разделы всё равно в витрине: {спрятанные}"


def test_the_switch_is_off():
    """Флаг выключен — иначе тест выше проходит по случайности.

    Отдельно, потому что первый молчит и при включённой персонализации, если
    вкусы человека случайно совпали с началом списка.
    """
    assert settings.personalise_catalogue is False, (
        "personalise_catalogue включён: витрина снова будет разной у разных людей"
    )
