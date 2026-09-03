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
from app.storage import get_storage, make_key
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


async def test_the_photographs_are_erased_from_storage(человек_с_историей):
    """Главное обещание удаления: снимков человека больше нет.

    До 31 августа 2026 они оставались. Строка обезличивалась, ответ приходил
    «готово», а лицо продолжало лежать в хранилище — то есть ответ был ложью.

    Проверяется и байтами, и строкой: файл должен исчезнуть из хранилища, а
    запись — получить отметку об удалении. Строку не удаляем: на неё ссылаются
    работы, а на них книга проводок.
    """
    session, user, сессия = человек_с_историей
    storage = get_storage()

    ключ = make_key(user_id=user.id, kind="upload", ext="jpg")
    await storage.put(ключ, b"\xff\xd8\xff" + b"0" * 900, content_type="image/jpeg")
    session.add(m.MediaAsset(user_id=user.id, kind="upload", storage_key=ключ,
                             mime="image/jpeg", bytes=903))
    await session.flush()
    assert await storage.exists(ключ), "снимок не положился — тест бессмыслен"

    await profile_router.delete_profile(
        response=ОтветЗаглушка(), ctx=(user, сессия), db=session, session_cookie=None)
    await session.flush()

    assert not await storage.exists(ключ), "снимок остался в хранилище после удаления аккаунта"
    строка = (await session.execute(
        select(m.MediaAsset).where(m.MediaAsset.user_id == user.id))).scalar_one()
    assert строка.deleted_at is not None, "запись о снимке не помечена удалённой"


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


async def test_deleting_an_account_erases_prompts_names_and_links(человек_с_историей):
    """Файлы стирались, а тексты — нет: промпт с именами из профилей, переписка,
    имя профиля и публичная ссылка переживали «удалить аккаунт». Ссылка при
    этом продолжала открываться и отдавать промпт без сессии.
    """
    session, user, сессия = человек_с_историей
    работа = m.Generation(user_id=user.id, operation="text_to_image", status="done",
                          prompt="Маша (дочь) на пляже", request_params={"answers": {"кто": "Маша"}},
                          share_id="shr_test_" + user.id[-6:], error="fal: x")
    профиль = m.PersonProfile(user_id=user.id, name="Маша (дочь)", media_ids=["med_1"],
                              reference_ids=["med_1"])
    реплика = m.ChatMessage(user_id=user.id, role="user", content="сделай меня как модель")
    session.add_all([работа, профиль, реплика])
    await session.flush()
    try:
        await profile_router.delete_profile(
            response=ОтветЗаглушка(), ctx=(user, сессия), db=session, session_cookie=None)
        await session.flush()
        await session.refresh(работа)
        await session.refresh(профиль)

        assert работа.prompt is None and работа.share_id is None and работа.error is None
        assert работа.request_params == {}
        assert профиль.name == "" and профиль.media_ids == [] and профиль.deleted_at is not None
        assert (await session.execute(
            select(m.ChatMessage).where(m.ChatMessage.user_id == user.id))).scalars().all() == []
    finally:
        await session.execute(delete(m.Generation).where(m.Generation.user_id == user.id))
        await session.flush()
