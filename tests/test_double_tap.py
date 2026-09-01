"""Двойное нажатие на «Generate».

Вопрос простой: спишется ли дважды. До этих двух мер — да.

Блокировка строки кошелька, поставленная от гонки в списании, здесь не
помогает и не должна: два списания по пятнадцать при балансе в сотню — это два
ЗАКОННЫХ списания, и кошелёк не может знать, что человек хотел один кадр.
Знает это только тот, кто видит намерение.

Мер две, и они закрывают разные окна:

* **замок** — два запроса одновременно (палец дважды);
* **ключ идемпотентности** — тот же запрос минутой позже (приложение не
  дождалось ответа на плохой связи и послало заново).

Замка одного мало: кадр рисуется в фоне, а запрос возвращается за три секунды,
и замок снимается вместе с ответом. Ключа одного мало: он живёт в базе, а
одновременные запросы успевают оба не найти друг друга.

    PYTHONPATH=. .venv/bin/python -m pytest tests/test_double_tap.py -q
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import delete

from app.core import rate_limit
from app.db import models as m
from app.db.repositories import generations as generations_repo
from app.db.session import connect, disconnect, get_factory
from app.redis_client import connect as redis_connect, disconnect as redis_disconnect

pytestmark = pytest.mark.asyncio


# ─── замок: одновременные нажатия ────────────────────────────────────────────


@pytest_asyncio.fixture
async def redis():
    await redis_connect()
    yield
    await redis_disconnect()


async def test_only_one_of_many_simultaneous_taps_gets_through(redis):
    """Десять одновременных — внутрь проходит один."""
    ключ = f"проба:{uuid.uuid4()}"
    прошли = await asyncio.gather(*[rate_limit.claim(ключ, 60) for _ in range(10)])
    assert sum(прошли) == 1, f"внутрь прошло {sum(прошли)} вместо одного"
    await rate_limit.let_go(ключ)


async def test_the_lock_holds_even_when_redis_is_far_away(redis, monkeypatch):
    """То же самое, но с задержкой на каждой команде к Redis.

    Без неё тест не проверяет ничего. Проверено: замок, разбитый на «посмотреть»
    и «занять», на localhost тоже пропускает ровно одного — команды успевают
    отработать раньше, чем следующая задача доберётся до своей. Стоит добавить
    две миллисекунды, то есть настоящую сеть до Redis в кластере, и внутрь
    проходят все десять.

    Ровно та же ловушка, что с холодным пулом соединений в пробе про кошелёк:
    зелёный результат означал скорость петли, а не защищённость кода.

    Задержка убирает везение и оставляет вопрос: неделима ли операция. `SET NX`
    неделим — он одна команда; «посмотреть, потом занять» — две, и между ними
    влезает кто угодно.
    """
    from app.redis_client import get_client

    настоящий = get_client()

    class Медленный:
        """Тот же клиент, но каждая команда идёт через сеть."""

        def __getattr__(self, имя):
            метод = getattr(настоящий, имя)

            async def медленно(*args, **kwargs):
                await asyncio.sleep(0.002)
                return await метод(*args, **kwargs)

            return медленно

    monkeypatch.setattr(rate_limit, "get_client", lambda: Медленный())

    ключ = f"проба:{uuid.uuid4()}"
    прошли = await asyncio.gather(*[rate_limit.claim(ключ, 60) for _ in range(10)])
    assert sum(прошли) == 1, (
        f"с задержкой внутрь прошло {sum(прошли)} вместо одного — "
        "значит замок не неделим"
    )
    await настоящий.delete(f"lock:{ключ}")


async def test_the_lock_is_released_and_the_next_order_is_allowed(redis):
    """Второй кадр после первого — законное желание, а не двойное нажатие."""
    ключ = f"проба:{uuid.uuid4()}"
    assert await rate_limit.claim(ключ, 60) is True
    assert await rate_limit.claim(ключ, 60) is False
    await rate_limit.let_go(ключ)
    assert await rate_limit.claim(ключ, 60) is True
    await rate_limit.let_go(ключ)


async def test_the_lock_always_expires_on_its_own(redis):
    """Держателя могут убить между «занял» и «снял».

    Без срока жизни человек остался бы заперт навсегда — и не своей виной, а
    нашим падением.
    """
    from app.redis_client import get_client

    ключ = f"проба:{uuid.uuid4()}"
    await rate_limit.claim(ключ, 42)
    осталось = await get_client().ttl(f"lock:{ключ}")
    assert 0 < осталось <= 42, "у замка нет срока жизни"
    await rate_limit.let_go(ключ)


# ─── ключ: повтор того же запроса ────────────────────────────────────────────


@pytest_asyncio.fixture
async def db():
    await connect()
    factory = get_factory()
    async with factory() as session:
        user = m.User(kind="guest")
        session.add(user)
        await session.flush()
        await session.commit()
        uid = user.id

        yield session, uid

        await session.execute(delete(m.Generation).where(m.Generation.user_id == uid))
        await session.execute(delete(m.User).where(m.User.id == uid))
        await session.commit()
    await disconnect()


async def test_the_same_key_finds_the_order_already_placed(db):
    session, uid = db
    ключ = str(uuid.uuid4())
    первый = await generations_repo.create(
        session, user_id=uid, operation="text_to_image", status="running",
        cost=15, idempotency_key=ключ)
    await session.flush()

    нашли = await generations_repo.get_by_idempotency_key(session, user_id=uid, key=ключ)
    assert нашли is not None and нашли.id == первый.id


async def test_a_different_key_is_a_different_order(db):
    """Иначе человек не смог бы заказать второй кадр вообще."""
    session, uid = db
    await generations_repo.create(
        session, user_id=uid, operation="text_to_image", status="running",
        cost=15, idempotency_key=str(uuid.uuid4()))
    await session.flush()

    нашли = await generations_repo.get_by_idempotency_key(
        session, user_id=uid, key=str(uuid.uuid4()))
    assert нашли is None


async def test_a_key_belongs_to_its_owner(db):
    """Чужой ключ не должен отдавать чужой заказ.

    Ключ придумывает клиент, и совпадение возможно — случайно или нарочно.
    Поэтому ищется он всегда вместе с владельцем.
    """
    session, uid = db
    ключ = str(uuid.uuid4())
    чужой = m.User(kind="guest")
    session.add(чужой)
    await session.flush()
    await generations_repo.create(
        session, user_id=чужой.id, operation="text_to_image", status="running",
        cost=15, idempotency_key=ключ)
    await session.flush()

    нашли = await generations_repo.get_by_idempotency_key(session, user_id=uid, key=ключ)
    assert нашли is None, "ключ отдал чужой заказ"

    await session.execute(delete(m.Generation).where(m.Generation.user_id == чужой.id))
    await session.execute(delete(m.User).where(m.User.id == чужой.id))


async def test_the_database_refuses_a_second_order_under_the_same_key(db):
    """Уникальный индекс, а не только проверка в коде.

    Между «поискал» и «записал» успевает влезть второй запрос — щель узкая, но
    она есть, и закрывает её база, а не аккуратность.
    """
    from sqlalchemy.exc import IntegrityError

    session, uid = db
    ключ = str(uuid.uuid4())
    await generations_repo.create(
        session, user_id=uid, operation="text_to_image", status="running",
        cost=15, idempotency_key=ключ)
    await session.flush()

    with pytest.raises(IntegrityError):
        await generations_repo.create(
            session, user_id=uid, operation="text_to_image", status="running",
            cost=15, idempotency_key=ключ)
        await session.flush()
    await session.rollback()


async def test_an_order_without_a_key_is_still_allowed(db):
    """Старая сборка приложения ключа не шлёт.

    Ломать работающих людей ради защиты, которую они не умеют попросить, нельзя:
    такой запрос обрабатывается как раньше.
    """
    session, uid = db
    for _ in range(3):
        await generations_repo.create(
            session, user_id=uid, operation="text_to_image", status="running", cost=15)
    await session.flush()  # уникальный индекс не должен считать NULL совпадением
