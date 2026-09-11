"""Вебхук Adapty: второй путь к тем же монетам.

Главный путь — чек от приложения и уведомления Apple напрямую. Adapty полезен
там, где первый молчит, и дублирование безопасно: подписка ищется по номеру
исходной транзакции, а пополнение квоты идемпотентно по номеру периода.

Проверяем: закрытую без секрета ручку, чужой заголовок, проверочный запрос из
панели, заведение подписки и монеты, повтор события, события про незнакомого
человека и события, которые состояние не меняют.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from app.config import settings
from app.db import models as m
from app.db.repositories import users as users_repo
from app.db.repositories import wallet as wallet_repo
from app.db.session import connect, disconnect, get_factory
from app.main import app
from app.services import wallet

SECRET = "Bearer test-secret-value"


def _event(kind: str, *, user_id: str, transaction: str, product: str = "week_6.99") -> dict:
    now = datetime.now(timezone.utc)
    return {
        "event_type": kind,
        "event_datetime": now.isoformat(),
        "customer_user_id": user_id,
        "profile_id": "11111111-2222-3333-4444-555555555555",
        "event_properties": {
            "profile_event_id": "66666666-7777-8888-9999-000000000000",
            "transaction_id": transaction,
            "original_transaction_id": transaction,
            "vendor_product_id": product,
            "purchase_date": now.isoformat(),
            "expires_date": (now + timedelta(days=7)).isoformat(),
            "environment": "Sandbox",
        },
    }


@pytest_asyncio.fixture
async def buyer():
    settings.adapty_webhook_secret = SECRET
    await connect()
    async with get_factory()() as db:
        user = await users_repo.create_guest(db)
        await wallet_repo.grant(db, user.id, amount=settings.signup_toontoon_balance,
                                bucket="free", reason="signup",
                                idempotency_key=f"signup:{user.id}")
        await db.commit()
        user_id = user.id
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, user_id
    async with get_factory()() as db:
        for table in (m.WalletLedger, m.Subscription, m.WalletBalance):
            await db.execute(delete(table).where(table.user_id == user_id))
        await db.execute(delete(m.User).where(m.User.id == user_id))
        await db.commit()
    await disconnect()
    settings.adapty_webhook_secret = ""


async def _balance(user_id: str) -> int:
    async with get_factory()() as db:
        return (await wallet.get_balance(db, user_id)).available


@pytest.mark.asyncio
async def test_wrong_authorization_is_refused(buyer) -> None:
    client, user_id = buyer
    event = _event("subscription_started", user_id=user_id, transaction="t-1")
    for header in ({}, {"Authorization": "Bearer wrong"}):
        answer = await client.post("/api/webhooks/adapty", json=event, headers=header)
        assert answer.status_code == 401
    assert await _balance(user_id) == settings.signup_toontoon_balance


@pytest.mark.asyncio
async def test_verification_request_answers_json(buyer) -> None:
    client, _ = buyer
    answer = await client.post("/api/webhooks/adapty", json={},
                               headers={"Authorization": SECRET})
    assert answer.status_code == 200 and answer.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_subscription_started_grants_coins(buyer) -> None:
    client, user_id = buyer
    event = _event("subscription_started", user_id=user_id, transaction=f"t-{user_id[-8:]}")
    answer = await client.post("/api/webhooks/adapty", json=event,
                               headers={"Authorization": SECRET})
    assert answer.status_code == 200 and answer.json()["status"] == "applied"
    assert await _balance(user_id) == settings.signup_toontoon_balance + 700

    # Повторная доставка того же события не даёт вторых монет.
    again = await client.post("/api/webhooks/adapty", json=event,
                              headers={"Authorization": SECRET})
    assert again.status_code == 200
    assert await _balance(user_id) == settings.signup_toontoon_balance + 700


@pytest.mark.asyncio
async def test_refund_marks_subscription(buyer) -> None:
    client, user_id = buyer
    transaction = f"r-{user_id[-8:]}"
    await client.post("/api/webhooks/adapty",
                      json=_event("subscription_started", user_id=user_id,
                                  transaction=transaction),
                      headers={"Authorization": SECRET})
    answer = await client.post("/api/webhooks/adapty",
                               json=_event("subscription_refunded", user_id=user_id,
                                           transaction=transaction),
                               headers={"Authorization": SECRET})
    assert answer.json() == {"status": "applied", "subscription": "refunded"}
    async with get_factory()() as db:
        from sqlalchemy import select
        row = await db.scalar(select(m.Subscription).where(m.Subscription.user_id == user_id))
        assert row is not None and row.status == "refunded"


@pytest.mark.asyncio
async def test_unknown_user_and_silent_events(buyer) -> None:
    client, user_id = buyer
    stranger = _event("subscription_started", user_id="usr_" + "f" * 32, transaction="t-x")
    answer = await client.post("/api/webhooks/adapty", json=stranger,
                               headers={"Authorization": SECRET})
    assert answer.json() == {"status": "unknown-user"}

    quiet = _event("subscription_deferred", user_id=user_id, transaction="t-y")
    answer = await client.post("/api/webhooks/adapty", json=quiet,
                               headers={"Authorization": SECRET})
    assert answer.json() == {"status": "ignored"}
    assert await _balance(user_id) == settings.signup_toontoon_balance
