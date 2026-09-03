"""Состояние подписки: чем оно решается и чем не решается.

Аудит нашёл здесь дыру, которая стоила бы денег напрямую. `_fields()` ставил
`status="active"` жёстко, по факту наличия чека, а чек предъявляется при каждом
запуске приложения — значит человек, которому вернули деньги, показывал
сохранённую строку JWS ещё раз, и подписка снова становилась действующей.
Отозвать чек нельзя: он у него на руках навсегда.

Рядом жила вторая: `current_period_end` писался в базу и не читался нигде во
всём коде, поэтому просроченный чек давал действующую подписку год спустя.

    PYTHONPATH=. .venv/bin/python -m pytest tests/test_subscription_state.py -q
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import delete

from app.db import models as m
from app.db.repositories import subscriptions as subs
from app.db.session import connect, disconnect, get_factory

pytestmark = pytest.mark.asyncio

СЕЙЧАС = datetime.now(timezone.utc)


def мс(moment: datetime) -> int:
    """Время в чеке — миллисекунды с начала эпохи."""
    return int(moment.timestamp() * 1000)


def чек(*, кончается: datetime, куплено: datetime | None = None, возврат=False) -> dict:
    payload = {
        "originalTransactionId": "2000000111222333",
        "productId": "ai.toontoon.pro.year",
        "bundleId": "ai.toontoon.ios",
        "environment": "Production",
        "purchaseDate": мс(куплено or (кончается - timedelta(days=365))),
        "expiresDate": мс(кончается),
    }
    if возврат:
        payload["revocationDate"] = мс(СЕЙЧАС - timedelta(days=1))
        payload["revocationReason"] = 1
    return payload


@pytest_asyncio.fixture
async def db():
    await connect()
    factory = get_factory()
    async with factory() as session:
        user = m.User(kind="registered")
        session.add(user)
        await session.flush()
        await session.commit()
        uid = user.id

        yield session, uid

        await session.execute(delete(m.Subscription).where(m.Subscription.user_id == uid))
        await session.execute(delete(m.User).where(m.User.id == uid))
        await session.commit()
    await disconnect()


# ─── что чек говорит о себе сам ──────────────────────────────────────────────


async def test_a_revoked_receipt_says_so_itself():
    """Возврат Apple помечает в самой транзакции — читать это надо оттуда."""
    assert subs.status_from_receipt(чек(кончается=СЕЙЧАС + timedelta(days=300),
                                        возврат=True)) == "refunded"


async def test_an_expired_receipt_is_expired():
    """Срок вышел — значит вышел, сколько бы раз чек ни предъявили."""
    assert subs.status_from_receipt(чек(кончается=СЕЙЧАС - timedelta(days=1))) == "expired"


async def test_a_live_receipt_is_active():
    assert subs.status_from_receipt(чек(кончается=СЕЙЧАС + timedelta(days=30))) == "active"


# ─── что делает предъявление чека ────────────────────────────────────────────


async def test_a_replayed_receipt_does_not_undo_a_refund(db):
    """Главная находка: повтор своей же сохранённой строки после возврата.

    Чек настоящий, свой, подписанный Apple. Раньше `refresh` раскладывал его
    поля по строке подписки вместе с жёстким `status="active"` — и возврат
    отменялся одним запросом.
    """
    session, uid = db
    исходный = чек(кончается=СЕЙЧАС + timedelta(days=300))
    row = await subs.bind(session, user_id=uid, payload=исходный)
    assert row.status == "active"

    await subs.apply_notification(session, transaction=исходный,
                                  status=subs.status_for("REFUND"))
    assert row.status == "refunded"

    # Тот же самый чек, предъявленный ещё раз.
    await subs.refresh(session, row, payload=исходный)
    assert row.status == "refunded", "повтор чека отменил возврат денег"
    assert await subs.active_for_user(session, uid) is None


async def test_a_genuine_renewal_does_restore_access(db):
    """Обратная сторона: человек, который подписался снова, доступ получает.

    Иначе лечение оказалось бы хуже болезни — «возврат навсегда» запер бы
    вернувшегося покупателя. Отличает эти два случая срок в чеке: продление
    приносит его дальше прежнего, а повтор — тот же самый. Подделать срок
    нельзя, тело подписано Apple.
    """
    session, uid = db
    старый = чек(кончается=СЕЙЧАС + timedelta(days=10))
    row = await subs.bind(session, user_id=uid, payload=старый)
    await subs.apply_notification(session, transaction=старый,
                                  status=subs.status_for("REFUND"))
    assert row.status == "refunded"

    новый = чек(кончается=СЕЙЧАС + timedelta(days=375), куплено=СЕЙЧАС)
    await subs.refresh(session, row, payload=новый)
    assert row.status == "active"
    assert await subs.active_for_user(session, uid) is not None


async def test_an_expired_period_is_not_an_active_subscription(db):
    """Строка может говорить «active», а срок при этом уже кончиться.

    Состояние ставится по чеку и по уведомлениям, а срок кончается сам, между
    ними. Пока `current_period_end` не читался никем, просроченная подписка
    оставалась действующей вечно и продолжала пополнять недельную квоту.
    """
    session, uid = db
    row = await subs.bind(session, user_id=uid,
                          payload=чек(кончается=СЕЙЧАС + timedelta(days=30)))
    assert await subs.active_for_user(session, uid) is not None

    # Срок вышел, а состояние в строке никто не менял.
    row.current_period_end = СЕЙЧАС - timedelta(minutes=1)
    row.status = "active"
    await session.flush()

    assert await subs.active_for_user(session, uid) is None


async def test_a_receipt_that_already_expired_never_becomes_active(db):
    """Первое предъявление просроченного чека тоже не открывает подписку."""
    session, uid = db
    row = await subs.bind(session, user_id=uid,
                          payload=чек(кончается=СЕЙЧАС - timedelta(days=400)))
    assert row.status == "expired"
    assert await subs.active_for_user(session, uid) is None


# ─── чеки из песочницы TestFlight ────────────────────────────────────────────

def _env_check(monkeypatch, *, sandbox_ok: bool, env: str):
    from app.services import app_store
    from app.config import settings
    monkeypatch.setattr(settings, "accept_storekit_test_root", False, raising=False)
    monkeypatch.setattr(settings, "debug", False, raising=False)
    monkeypatch.setattr(settings, "accept_sandbox_receipts", sandbox_ok, raising=False)
    return lambda: app_store._check_environment({"environment": env}, "чек")


def test_песочница_отвергается_по_умолчанию(monkeypatch):
    import pytest
    from app.services.app_store import BadTransaction
    with pytest.raises(BadTransaction):
        _env_check(monkeypatch, sandbox_ok=False, env="Sandbox")()


def test_песочница_принимается_только_с_флагом(monkeypatch):
    _env_check(monkeypatch, sandbox_ok=True, env="Sandbox")()      # не бросает
    _env_check(monkeypatch, sandbox_ok=True, env="Production")()


def test_флаг_песочницы_не_открывает_прочее(monkeypatch):
    import pytest
    from app.services.app_store import BadTransaction
    with pytest.raises(BadTransaction):
        _env_check(monkeypatch, sandbox_ok=True, env="Xcode")()
    with pytest.raises(BadTransaction):
        _env_check(monkeypatch, sandbox_ok=True, env="")()
