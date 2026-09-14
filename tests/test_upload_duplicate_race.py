"""Один и тот же снимок, приехавший дважды одновременно, не ломает загрузку.

Приложение шлёт снимки профиля пачками по четыре. Если человек выбрал один
кадр дважды, обе копии доходят до проверки на дубликат раньше, чем первая
успевает записаться: проверка их не видит, а уникальный индекс — видит, и
одна загрузка падала с 400 (Илья, 2026-09-14).
"""
from __future__ import annotations

import asyncio
import io

import pytest
import pytest_asyncio
from PIL import Image
from sqlalchemy import delete

from app.db import models as m
from app.db.repositories import media as media_repo
from app.db.repositories import users as users_repo
from app.db.session import connect, disconnect, get_factory


def _photo() -> bytes:
    image = Image.new("RGB", (600, 800), (40, 60, 90))
    buf = io.BytesIO()
    image.save(buf, "JPEG", quality=80)
    return buf.getvalue()


@pytest_asyncio.fixture
async def person():
    await connect()
    async with get_factory()() as db:
        user = await users_repo.create_guest(db)
        await db.commit()
        user_id = user.id
    yield user_id
    async with get_factory()() as db:
        await db.execute(delete(m.MediaAsset).where(m.MediaAsset.user_id == user_id))
        await db.execute(delete(m.User).where(m.User.id == user_id))
        await db.commit()
    await disconnect()


@pytest.mark.asyncio
async def test_same_photo_twice_at_once(person) -> None:
    data = _photo()

    async def save():
        async with get_factory()() as db:
            asset = await media_repo.save_image(db, user_id=person, kind="upload", data=data)
            await db.commit()
            return asset.id

    first, second = await asyncio.gather(save(), save())
    # Обе загрузки успешны и указывают на один и тот же файл: второй копии
    # хранилище не получает, а человек не видит ошибки.
    assert first == second


@pytest.mark.asyncio
async def test_same_photo_twice_in_a_row(person) -> None:
    data = _photo()
    async with get_factory()() as db:
        first = await media_repo.save_image(db, user_id=person, kind="upload", data=data)
        second = await media_repo.save_image(db, user_id=person, kind="upload", data=data)
        await db.commit()
    assert first.id == second.id
