"""Себестоимость: чем добирается цена кадра, о которой провайдер промолчал.

По этому числу ставят цену подписки, и до сих пор его никто не проверял —
тестов на сводку не было ни одного. Молчание обошлось в 43 процента: сводка
показывала $0.0294 за TOONTOON вместо настоящих $0.0516.

Занижали двое. Кадры `openrouter_gemini_pro` не считались вовсе — ориентира у
строки не было. А у `openrouter_gpt` ориентир был, $0.042, и в этом оказалось
хуже: одиночный замер с одного лабораторного кадра — не цена, а нижняя
граница. Внутри одной модели кадр стоит от $0.0334 до $0.1090, потому что у
живых запросов разные длина промпта и число референсов:

    модель                     в доке    минимум   медиана   разброс
    gpt-image-2                $0.042    $0.0334   $0.0667     3.3x
    gemini-3.1-flash-image     $0.068    $0.0680   $0.2048     3.0x
    gemini-3-pro-image         $0.137    $0.1349   $0.1379     2.1x

Числа из документации легли на минимум, а не на медиану — по всем трём
строкам. Такой ориентир занижает по построению.

Отсюда лестница источников, и порядок в ней — суть проверки:

    факт  ->  медиана СВОИХ замеров за тот же срок  ->  ориентир реестра  ->  ничего

Медиана стоит выше ориентира потому, что сложена из тех самых запросов,
которые считаем, и следует за ними сама. Застывшее число не может.

Тест зовёт те же константы, что и сам скрипт, а не пересказывает запрос своими
словами: пересказ продолжает проходить и после того, как настоящий запрос
сломали.

    PYTHONPATH=. .venv/bin/python -m pytest tests/test_cost_report.py -q
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import delete, text

from app.db import models as m
from app.db.session import connect, disconnect, get_factory
from scripts.health import ECONOMICS_SQL, PROVIDER_COSTS_SQL

pytestmark = pytest.mark.asyncio

ПРОВАЙДЕР = "test_cost_ladder"
ЗАМЕР = 0.10      # столько провайдер назвал сам
ПРАЙС = 0.01      # столько обещал прайс — вдесятеро меньше правды


@pytest_asyncio.fixture
async def db():
    await connect()
    factory = get_factory()
    async with factory() as session:
        # Уборка ПЕРЕД работой, а не только после: если прошлый прогон умер на
        # полпути, строка провайдера пережила его и следующий запуск падал бы
        # на «duplicate key» — то есть тест ломался бы от собственного мусора,
        # а не от кода, который проверяет.
        await session.execute(
            delete(m.Generation).where(m.Generation.provider_id == ПРОВАЙДЕР))
        await session.execute(
            delete(m.GenerationProvider).where(m.GenerationProvider.id == ПРОВАЙДЕР))
        await session.commit()

        user = m.User(kind="guest")
        session.add(user)
        await session.flush()
        # Снять id ДО commit. После него объект протухает, и обращение к
        # `user.id` из уборки лезет за ним в базу — уже вне контекста, в
        # котором SQLAlchemy умеет ждать. Отсюда MissingGreenlet, и падал
        # тогда не тест, а его уборка, оставляя мусор следующему прогону.
        uid = user.id
        session.add(m.GenerationProvider(
            id=ПРОВАЙДЕР, operations=["text_to_image"], model="ненастоящая",
            priority=999, is_enabled=False,
            cost_hint={"usd_per_image": ПРАЙС},
        ))
        await session.commit()
        try:
            yield session, uid
        finally:
            await session.execute(
                delete(m.Generation).where(m.Generation.provider_id == ПРОВАЙДЕР))
            await session.execute(
                delete(m.GenerationProvider).where(m.GenerationProvider.id == ПРОВАЙДЕР))
            await session.execute(delete(m.User).where(m.User.id == uid))
            await session.commit()
    await disconnect()


async def кадры(session, user_id: str, *, с_ценой: int, без_цены: int) -> None:
    """Работы одного провайдера: часть с названной ценой, часть без неё."""
    for i in range(с_ценой + без_цены):
        session.add(m.Generation(
            user_id=user_id, operation="text_to_image", provider_id=ПРОВАЙДЕР,
            status="done", cost=1, request_params={},
            provider_cost_usd=ЗАМЕР if i < с_ценой else None,
            finished_at=text("now()"),
        ))
    await session.commit()


async def строка(session):
    rows = (await session.execute(text(PROVIDER_COSTS_SQL), {"d": 1})).all()
    наша = [r for r in rows if r[0] == ПРОВАЙДЕР]
    assert наша, "провайдера нет в сводке"
    return наша[0]


async def test_молчание_добирается_медианой_а_не_прайсом(db):
    """Три кадра с ценой и два без. Двум безмолвным ставят медиану трёх."""
    session, uid = db
    await кадры(session, uid, с_ценой=3, без_цены=2)

    _, n, _, avg_usd, sum_usd, from_median, from_list, unknown = await строка(session)

    assert n == 5
    assert (from_median, from_list, unknown) == (2, 0, 0), \
        "два безмолвных кадра должны считаться по медиане, а не по прайсу"
    # 5 × 0.10. По прайсу вышло бы 3×0.10 + 2×0.01 = 0.32 — вдвое меньше.
    assert float(sum_usd) == pytest.approx(0.50, abs=1e-6)
    assert float(avg_usd) == pytest.approx(0.10, abs=1e-6)


async def test_прайс_остаётся_последней_опорой(db):
    """Замеров нет ни одного — тогда и только тогда в ход идёт прайс."""
    session, uid = db
    await кадры(session, uid, с_ценой=0, без_цены=4)

    _, n, _, avg_usd, sum_usd, from_median, from_list, unknown = await строка(session)

    assert (n, from_median, from_list, unknown) == (4, 0, 4, 0)
    assert float(sum_usd) == pytest.approx(4 * ПРАЙС, abs=1e-6)


async def test_без_цены_и_без_прайса_кадр_честно_не_учтён(db):
    """Ни замера, ни прайса — кадр не подмешивается выдуманным числом."""
    session, uid = db
    await session.execute(
        text("UPDATE generation_providers SET cost_hint = NULL WHERE id = :p"),
        {"p": ПРОВАЙДЕР})
    await кадры(session, uid, с_ценой=0, без_цены=3)

    _, n, _, avg_usd, sum_usd, from_median, from_list, unknown = await строка(session)

    assert (n, from_median, from_list, unknown) == (3, 0, 0, 3)
    assert sum_usd is None, "выдумывать цену нельзя — пусть будет прочерк"


async def test_себестоимость_считает_по_той_же_лестнице(db):
    """`economics()` не должна расходиться с `providers()` в способе счёта."""
    session, uid = db
    await кадры(session, uid, с_ценой=3, без_цены=2)

    _, _, _, _, guessed, unknown = (
        await session.execute(text(ECONOMICS_SQL), {"d": 1})).one()

    assert guessed >= 2, "два безмолвных кадра должны попасть в долю оценки"
    # Наши кадры учтены — в неучтённые они попасть не могли.
    assert unknown == 0 or unknown < guessed
