"""Набор дня: закреплённая голова и вращающийся хвост.

Первое, что человек видит на вкладке Shots, выбираем мы: девушка, она же в
мультфильме, парень, он же в мультфильме. По этим четырём карточкам за секунду
читается, что здесь делают и на ком это работает, — отдавать такое остатку от
деления по дате незачем. Остальные крутятся по дню, как и раньше.

    PYTHONPATH=. .venv/bin/python -m pytest tests -q
"""
from __future__ import annotations

from datetime import date

import pytest
import pytest_asyncio
from sqlalchemy import delete

from app.config import settings
from app.db import models as m
from app.db.repositories import styles as styles_repo
from app.db.session import connect, disconnect, get_factory

pytestmark = pytest.mark.asyncio

ЗАКРЕПЛЁННЫЕ = ["luxury_rooftop", "plant_room", "warm_knit", "bedroom_beats"]


@pytest_asyncio.fixture
async def каталог():
    """Сорок кадров, среди них — четвёрка витрины."""
    await connect()
    async with get_factory()() as session:
        ids = [f"t_shot_{i:02d}" for i in range(36)] + ЗАКРЕПЛЁННЫЕ
        for style_id in ids:
            session.add(m.Style(id=f"zz_{style_id}", title=style_id, category="test",
                                operation="image_to_image", input_spec={}, cost=15,
                                prompt_template={}, is_shot=True, is_active=True))
        await session.flush()
        yield session, ids
        await session.execute(delete(m.Style).where(m.Style.id.like("zz_%")))
        await session.commit()
    await disconnect()


@pytest.fixture(autouse=True)
def свои_настройки(monkeypatch):
    # Тестовый каталог живёт под своим префиксом, чтобы не задеть настоящий.
    monkeypatch.setattr(settings, "shots_pinned_ids",
                        ",".join(f"zz_{s}" for s in ЗАКРЕПЛЁННЫЕ))
    monkeypatch.setattr(settings, "shots_per_day", 30)


async def test_the_four_come_first_on_any_day(каталог):
    """Голова набора не крутится: она одна и та же и первого числа, и сотого."""
    session, _ = каталог
    ожидание = [f"zz_{s}" for s in ЗАКРЕПЛЁННЫЕ]
    for day in (date(2026, 9, 15), date(2026, 9, 16), date(2027, 3, 3)):
        got = await styles_repo.daily_shots(session, day)
        assert [row.id for row in got][:4] == ожидание


async def test_the_set_is_thirty_without_repeats(каталог):
    """Тридцать карточек и ни одной дважды.

    Закреплённые лежат в том же пуле, что и хвост: не вычеркнув их оттуда,
    мы показали бы витрину дважды — сверху и где-то в середине.
    """
    session, _ = каталог
    got = await styles_repo.daily_shots(session, date(2026, 9, 15))
    ids = [row.id for row in got]
    assert len(ids) == settings.shots_per_day
    assert len(set(ids)) == len(ids)


async def test_the_tail_moves_with_the_day(каталог):
    """«Новая подборка каждый день» — про хвост, и он действительно другой."""
    session, _ = каталог
    вчера = [r.id for r in await styles_repo.daily_shots(session, date(2026, 9, 15))]
    сегодня = [r.id for r in await styles_repo.daily_shots(session, date(2026, 9, 16))]
    assert вчера[:4] == сегодня[:4]
    assert вчера[4:] != сегодня[4:]


async def test_a_hand_written_day_wins_and_keeps_its_order(каталог):
    """Набор, собранный руками на дату, показывается как записан."""
    session, _ = каталог
    day = date(2026, 12, 31)
    порядок = ["zz_t_shot_05", "zz_warm_knit", "zz_t_shot_01"]
    session.add(m.ShotsSchedule(day=day, style_ids=порядок))
    await session.flush()
    try:
        got = await styles_repo.daily_shots(session, day)
        assert [row.id for row in got] == порядок
    finally:
        await session.execute(delete(m.ShotsSchedule).where(m.ShotsSchedule.day == day))
        await session.flush()
