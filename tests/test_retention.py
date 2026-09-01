"""Срок хранения снимков заброшенного гостя.

Срока не было вовсе: фотографии лиц лежали вечно. Хуже: уборка заброшенных
гостей упоминалась в трёх комментариях как существующая — ради неё исправно
писался `last_seen_at`, — а читать это было некому.

Гость это человек, не оставивший нам ни почты, ни покупки: спросить, нужны ли
ему ещё его снимки, невозможно. Поэтому у них срок есть, а у
зарегистрированных нет: те распоряжаются своими данными сами.

    PYTHONPATH=. .venv/bin/python -m pytest tests/test_retention.py -q
"""
from __future__ import annotations

import io
from datetime import timedelta

import pytest
import pytest_asyncio
from PIL import Image
from sqlalchemy import delete, func, select

from app.db import models as m
from app.db.repositories import media as media_repo
from app.db.repositories import users as users_repo
from app.db.session import connect, disconnect, get_factory
from app import storage

pytestmark = pytest.mark.asyncio

СРОК = timedelta(days=180)


def картинка() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), (100, 140, 90)).save(buf, format="PNG")
    return buf.getvalue()


@pytest_asyncio.fixture
async def db():
    await connect()
    await storage.startup()
    factory = get_factory()
    заведённые: list[str] = []
    async with factory() as session:
        yield session, заведённые
        for uid in заведённые:
            await session.execute(delete(m.MediaAsset).where(m.MediaAsset.user_id == uid))
            await session.execute(delete(m.WalletLedger).where(m.WalletLedger.user_id == uid))
            await session.execute(delete(m.WalletBalance).where(m.WalletBalance.user_id == uid))
            await session.execute(delete(m.User).where(m.User.id == uid))
        await session.commit()
    await disconnect()


async def завести(session, заведённые, *, kind="guest", дней_назад=0, со_снимком=True):
    человек = m.User(kind=kind)
    session.add(человек)
    await session.flush()
    заведённые.append(человек.id)
    if со_снимком:
        await media_repo.save_image(session, user_id=человек.id, kind="upload",
                                    data=картинка())
    if дней_назад:
        человек.last_seen_at = func.now() - timedelta(days=дней_назад)
    else:
        человек.last_seen_at = func.now()
    await session.flush()
    return человек


async def test_a_long_abandoned_guest_is_collected(db):
    session, заведённые = db
    гость = await завести(session, заведённые, дней_назад=200)
    найдены = await users_repo.abandoned_guests(session, older_than=СРОК)
    assert гость.id in {g.id for g in найдены}


async def test_a_guest_who_came_back_recently_is_left_alone(db):
    """Полгода — это заведомо больше любого разумного «вернусь позже»."""
    session, заведённые = db
    гость = await завести(session, заведённые, дней_назад=30)
    найдены = await users_repo.abandoned_guests(session, older_than=СРОК)
    assert гость.id not in {g.id for g in найдены}


async def test_a_registered_account_is_never_collected(db):
    """Их данные — их. У них есть кнопка удаления, и решают они сами."""
    session, заведённые = db
    человек = await завести(session, заведённые, kind="registered", дней_назад=900)
    найдены = await users_repo.abandoned_guests(session, older_than=СРОК)
    assert человек.id not in {g.id for g in найдены}


async def test_a_guest_merged_into_an_account_is_never_collected(db):
    """Его снимки переехали к живому человеку — стереть их значит стереть чужое."""
    session, заведённые = db
    гость = await завести(session, заведённые, дней_назад=400)
    гость.merged_into_user_id = гость.id
    await session.flush()
    найдены = await users_repo.abandoned_guests(session, older_than=СРОК)
    assert гость.id not in {g.id for g in найдены}


async def test_a_guest_with_nothing_left_is_not_collected_again(db):
    """Иначе уборка возвращала бы одних и тех же людей вечно."""
    session, заведённые = db
    гость = await завести(session, заведённые, дней_назад=400)
    assert гость.id in {g.id for g in await users_repo.abandoned_guests(session, older_than=СРОК)}

    await media_repo.erase_everything_of(session, гость.id)
    assert гость.id not in {g.id for g in await users_repo.abandoned_guests(session, older_than=СРОК)}


async def test_collecting_erases_the_bytes_not_just_the_row(db):
    """«Удалено» обязано означать, что файла нет, — иначе это только вид."""
    session, заведённые = db
    гость = await завести(session, заведённые, дней_назад=400)
    актив = await session.scalar(
        select(m.MediaAsset).where(m.MediaAsset.user_id == гость.id))
    ключ = актив.storage_key
    assert await storage.get_storage().exists(ключ)

    await media_repo.erase_everything_of(session, гость.id)
    assert not await storage.get_storage().exists(ключ), "файл остался в хранилище"
