"""Обработка кадра не должна останавливать сервер.

`images.process` — чистый CPU: разобрать PNG, снять метаданные, пережать,
сделать миниатюру. На кадре в мегабайт это 875 мс. Вызванный прямо из
`async def`, он держал цикл событий колом всё это время — ни один запрос ни
одного человека не обрабатывался: ни баланс, ни опрос готовности, ни /health.
При десятках кадров в минуту сервер отвечал урывками.

Тест не меряет скорость. Он меряет, **живёт ли цикл, пока идёт обработка**:
рядом крутится тикер, и если за время `save_image` он успел тикнуть много раз
— цикл свободен; если ноль-один раз — цикл стоял. До правки тикер давал 0–1.

    PYTHONPATH=. .venv/bin/python -m pytest tests/test_loop_not_blocked.py -q
"""
from __future__ import annotations

import asyncio
import io
import pathlib

import pytest
import pytest_asyncio
from PIL import Image
from sqlalchemy import delete

from app import storage
from app.db import models as m
from app.db.repositories import media as media_repo
from app.db.session import connect, disconnect, get_factory

pytestmark = pytest.mark.asyncio

# Настоящий тяжёлый кадр, а не квадратик 32×32: на маленьком обработка
# занимает миллисекунды, и тест ничего не отличит.
КАДР = pathlib.Path("content/styles/black_and_white/white_suit/example-1.jpg")


@pytest_asyncio.fixture
async def db():
    await connect()
    await storage.startup()
    factory = get_factory()
    async with factory() as session:
        user = m.User(kind="guest")
        session.add(user)
        await session.flush()
        uid = user.id
        await session.commit()
        try:
            yield session, uid
        finally:
            await session.execute(delete(m.MediaAsset).where(m.MediaAsset.user_id == uid))
            await session.execute(delete(m.User).where(m.User.id == uid))
            await session.commit()
    await disconnect()


async def test_цикл_живёт_пока_обрабатывается_кадр(db):
    session, uid = db
    data = КАДР.read_bytes()
    assert len(data) > 500_000, "нужен тяжёлый кадр, иначе нечего мерить"

    тики = 0
    async def тикер():
        nonlocal тики
        while True:
            await asyncio.sleep(0.01)
            тики += 1

    t = asyncio.create_task(тикер())
    try:
        asset = await media_repo.save_image(session, user_id=uid, kind="generation", data=data)
    finally:
        t.cancel()
    assert asset.id

    # На 875 мс обработки при шаге 10 мс цикл, если свободен, тикнет десятки
    # раз. Порог занижен нарочно: важно отличить «стоял» от «шёл», а не
    # угадать точное число на этой машине.
    assert тики >= 20, f"цикл событий стоял: тикер успел лишь {тики} раз(а)"
