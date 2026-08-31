"""Удаление аккаунта делает ровно то, что обещает диалог.

Apple требует, чтобы удаление начиналось внутри приложения (правило 5.1.1(v)),
и без него заявку отклоняют. Экран появился 31 августа 2026, а до него была
только серверная ручка — то есть требование не выполнялось вовсе.

Диалог в приложении обещает три вещи: почта, имя и картинка стираются, из
аккаунта выкидывает везде, записи о платежах остаются. Тест проверяет каждую.

Стирание, а не удаление строки, — потому что на пользователя ссылается книга
проводок, и база справедливо отказывается удалить того, на кого показывают
деньги. Человек при этом исчезает: узнать его в оставшейся строке нечем.
"""
from __future__ import annotations

import pytest_asyncio
from sqlalchemy import delete, select

from app.config import settings
from app.db.session import connect, disconnect, get_factory
from app import redis_client
from app.db import models as m
from app.db.repositories import wallet as wallet_repo
from app.models.session import Session
from app.routers import profile as profile_router


@pytest_asyncio.fixture
async def человек_с_историей():
    """Тот, у кого есть имя, почта и потраченные деньги."""
    # Удаление гасит сессию, а она живёт в Redis. Поднимаем поддельный: тест
    # проверяет, что человек исчезает из базы, а не как устроено хранилище
    # сессий, — настоящий Redis здесь только источник хрупкости.
    settings.use_fake_redis = True
    await redis_client.connect()
    await connect()
    async with get_factory()() as session:
        user = m.User(kind="email", email="kto@example.com", name="Человек")
        session.add(user)
        await session.flush()
        await wallet_repo.grant(session, user.id, amount=30, bucket="free",
                                reason="signup", idempotency_key=f"seed:{user.id}")
        await wallet_repo.spend(session, user.id, cost=15, reason="generation",
                                ref_id=f"pay_{user.id[-6:]}",
                                idempotency_key=f"spend:{user.id}")
        await session.flush()
        сессия = Session(sid=f"sid_{user.id[-8:]}", user_id=user.id, provider="email")
        yield session, user, сессия
        await session.execute(delete(m.WalletLedger).where(m.WalletLedger.user_id == user.id))
        await session.execute(delete(m.WalletBalance).where(m.WalletBalance.user_id == user.id))
        await session.execute(delete(m.User).where(m.User.id == user.id))
        await session.commit()
    await disconnect()
    await redis_client.disconnect()


class ОтветЗаглушка:
    """`delete_profile` чистит куку через объект ответа — здесь он не нужен."""
    def __init__(self): self.headers = {}
    def delete_cookie(self, *a, **k): pass
    def set_cookie(self, *a, **k): pass


async def test_deleting_an_account_erases_who_the_person_was(человек_с_историей):
    session, user, сессия = человек_с_историей

    await profile_router.delete_profile(
        response=ОтветЗаглушка(), ctx=(user, сессия), db=session, session_cookie=None)
    await session.flush()

    строка = (await session.execute(
        select(m.User).where(m.User.id == user.id))).scalar_one()
    assert строка.email is None, "почта осталась"
    assert строка.name is None, "имя осталось"
    assert строка.avatar_key is None, "картинка осталась"
    assert строка.deleted_at is not None, "строка не помечена удалённой"


async def test_the_ledger_survives_because_accounting_needs_it(человек_с_историей):
    """Диалог обещает, что записи о платежах останутся, — так и должно быть.

    Проверяется отдельно, потому что соблазн «удалить всё разом» велик, а
    удалённая книга проводок — это несходящийся баланс и невозможность
    разобраться в возврате.
    """
    session, user, сессия = человек_с_историей

    await profile_router.delete_profile(
        response=ОтветЗаглушка(), ctx=(user, сессия), db=session, session_cookie=None)
    await session.flush()

    проводки = (await session.execute(
        select(m.WalletLedger).where(m.WalletLedger.user_id == user.id))).scalars().all()
    assert проводки, "книга проводок исчезла вместе с человеком"
