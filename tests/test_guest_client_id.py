"""Гость заводится с идентификатором, который придумало приложение.

Приложению идентификатор нужен раньше нашего ответа: с ним оно запускает
Adapty, иначе первые события покупки уезжают анонимными. Принимаем только
свой вид идентификатора и только свободный; занятый не отдаём.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import delete

from app.db import models as m
from app.db.session import connect, disconnect, get_factory
from app.db.repositories import users as users_repo


@pytest_asyncio.fixture
async def session():
    await connect()
    async with get_factory()() as db:
        made: list[str] = []
        yield db, made
        await db.execute(delete(m.WalletBalance).where(m.WalletBalance.user_id.in_(made)))
        await db.execute(delete(m.WalletLedger).where(m.WalletLedger.user_id.in_(made)))
        await db.execute(delete(m.User).where(m.User.id.in_(made)))
        await db.commit()
    await disconnect()


@pytest.mark.asyncio
async def test_client_id_is_used(session) -> None:
    db, made = session
    wanted = "usr_" + "a" * 32
    user = await users_repo.create_guest(db, user_id=wanted)
    made.append(user.id)
    assert user.id == wanted


@pytest.mark.asyncio
async def test_malformed_id_is_ignored(session) -> None:
    db, made = session
    for bad in ("", "usr_ЖЖ", "usr_" + "a" * 31, "adm_" + "a" * 32,
                "usr_" + "A" * 32, "'; drop table users; --"):
        user = await users_repo.create_guest(db, user_id=bad)
        made.append(user.id)
        assert user.id != bad and user.id.startswith("usr_")


@pytest.mark.asyncio
async def test_taken_id_is_not_handed_over(session) -> None:
    db, made = session
    wanted = "usr_" + "b" * 32
    first = await users_repo.create_guest(db, user_id=wanted)
    second = await users_repo.create_guest(db, user_id=wanted)
    made += [first.id, second.id]
    assert first.id == wanted and second.id != wanted


@pytest.mark.asyncio
async def test_no_id_still_works(session) -> None:
    db, made = session
    user = await users_repo.create_guest(db)
    made.append(user.id)
    assert user.id.startswith("usr_")
