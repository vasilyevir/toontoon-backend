"""Путь денег целиком: покупка → монеты → списание → продление → возврат.

Проверка по просьбе Ильи (2026-09-10): убедиться, что после подписки монеты
начисляются правильно, тратятся правильно и ровно то же число видит
приложение. Проверяется не одна функция, а цепочка — именно в стыках она и
рвалась: квота была написана и покрыта тестами, а вызвать её забыли.

Клиенту здесь не верят нигде: подписка появляется из чека, проверенного
подписью Apple, а число монет берётся из тарифа в нашей базе, а не из того,
что сказало приложение.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import delete

from app.config import settings
from app.db import models as m
from app.db.repositories import wallet as wallet_repo
from app.db.session import connect, disconnect, get_factory
from app.services import wallet


def _purchase(product_id: str, *, at: datetime, until: datetime, transaction: str) -> dict:
    """Чек в том виде, в каком его отдаёт проверка подписи Apple.

    Срок задаётся отдельно от даты покупки: подписка, купленная месяц назад,
    сегодня действует именно потому, что продлевалась.
    """
    return {
        "originalTransactionId": transaction,
        "transactionId": transaction,
        "productId": product_id,
        "purchaseDate": int(at.timestamp() * 1000),
        "expiresDate": int(until.timestamp() * 1000),
        "environment": "Sandbox",
    }


@pytest_asyncio.fixture
async def buyer():
    await connect()
    async with get_factory()() as db:
        user = m.User(kind="guest")
        db.add(user)
        await db.flush()
        await wallet_repo.grant(db, user.id, amount=settings.signup_toontoon_balance,
                                bucket="free", reason="signup",
                                idempotency_key=f"signup:{user.id}")
        await db.commit()
        yield db, user
        for table in (m.WalletLedger, m.Subscription, m.WalletBalance):
            await db.execute(delete(table).where(table.user_id == user.id))
        await db.execute(delete(m.User).where(m.User.id == user.id))
        await db.commit()
    await disconnect()


async def _subscribe(db, user, product_id: str, *, bought: datetime,
                     until: datetime | None = None):
    from app.db.repositories import subscriptions as subscriptions_repo
    payload = _purchase(product_id, at=bought,
                        until=until or datetime.now(timezone.utc) + timedelta(days=7),
                        transaction=f"t-{user.id[-12:]}")
    row = await subscriptions_repo.bind(db, user_id=user.id, payload=payload)
    await db.flush()
    return row


@pytest.mark.asyncio
async def test_weekly_grants_700_and_spending_shows_up(buyer) -> None:
    db, user = buyer
    start = await wallet.get_balance(db, user.id)
    assert start.available == settings.signup_toontoon_balance

    await _subscribe(db, user, "week_6.99", bought=datetime.now(timezone.utc) - timedelta(hours=1))
    await wallet.ensure_subscription_quota(db, user.id)

    after = await wallet.get_balance(db, user.id)
    assert after.available == settings.signup_toontoon_balance + 700

    # Кадр стоит столько, сколько объявлено в настройках, и списывается
    # сначала из подписки — свободные монеты остаются на потом.
    await wallet_repo.spend(db, user.id, cost=settings.image_toontoon_cost,
                            reason="generation", idempotency_key="gen-1")
    spent = await wallet.get_balance(db, user.id)
    assert spent.available == after.available - settings.image_toontoon_cost
    wallet_row = await wallet_repo.ensure(db, user.id)
    assert wallet_row.sub_balance == 700 - settings.image_toontoon_cost
    assert wallet_row.free_balance == settings.signup_toontoon_balance


@pytest.mark.asyncio
async def test_quota_does_not_double_inside_the_period(buyer) -> None:
    db, user = buyer
    await _subscribe(db, user, "week_6.99", bought=datetime.now(timezone.utc) - timedelta(days=2))
    for _ in range(3):
        await wallet.ensure_subscription_quota(db, user.id)
    assert (await wallet.get_balance(db, user.id)).available == \
        settings.signup_toontoon_balance + 700


@pytest.mark.asyncio
async def test_next_week_refills_to_the_full_quota(buyer) -> None:
    db, user = buyer
    bought = datetime.now(timezone.utc) - timedelta(days=9)
    await _subscribe(db, user, "week_6.99", bought=bought,
                     until=datetime.now(timezone.utc) + timedelta(days=30))
    await wallet.ensure_subscription_quota(db, user.id)
    await wallet_repo.spend(db, user.id, cost=100, reason="generation", idempotency_key="gen-2")
    # Новая неделя началась: остаток не складывается с новой квотой, а
    # заменяется ею — иначе месяц простоя превращался бы в накопленный банк.
    await wallet_repo.ensure_period_quota(
        db, user.id, quota=700, anchor=bought, period_days=7,
        now=datetime.now(timezone.utc) + timedelta(days=5))
    wallet_row = await wallet_repo.ensure(db, user.id)
    assert wallet_row.sub_balance == 700


@pytest.mark.asyncio
async def test_yearly_grants_3000_once(buyer) -> None:
    db, user = buyer
    bought = datetime.now(timezone.utc) - timedelta(days=30)
    await _subscribe(db, user, "year_39.99", bought=bought,
                     until=datetime.now(timezone.utc) + timedelta(days=335))
    await wallet.ensure_subscription_quota(db, user.id)
    assert (await wallet.get_balance(db, user.id)).available == \
        settings.signup_toontoon_balance + 3000
    # Через неделю ничего не добавляется: годовой тариф даёт монеты на год.
    await wallet.ensure_subscription_quota(db, user.id)
    assert (await wallet.get_balance(db, user.id)).available == \
        settings.signup_toontoon_balance + 3000


@pytest.mark.asyncio
async def test_refund_stops_the_quota(buyer) -> None:
    db, user = buyer
    row = await _subscribe(db, user, "week_6.99",
                           bought=datetime.now(timezone.utc) - timedelta(days=1))
    await wallet.ensure_subscription_quota(db, user.id)
    before = (await wallet.get_balance(db, user.id)).available

    # Деньги вернули — подписка больше не действует, и следующая неделя
    # монет не приносит.
    row.status = "refunded"
    await db.flush()
    await wallet.ensure_subscription_quota(db, user.id)
    assert (await wallet.get_balance(db, user.id)).available == before


@pytest.mark.asyncio
async def test_expired_subscription_grants_nothing(buyer) -> None:
    db, user = buyer
    row = await _subscribe(db, user, "week_6.99",
                           bought=datetime.now(timezone.utc) - timedelta(days=8))
    # Срок кончился и продления не было: доступ снимается по дате, даже если
    # уведомление об окончании до нас не доехало.
    row.current_period_end = datetime.now(timezone.utc) - timedelta(days=1)
    await db.flush()
    await wallet.ensure_subscription_quota(db, user.id)
    assert (await wallet.get_balance(db, user.id)).available == settings.signup_toontoon_balance


# ─── Покупка, о которой приложение не рассказало ─────────────────────────────

@pytest.mark.asyncio
async def test_webhook_binds_purchase_by_account_token(buyer) -> None:
    """Между оплатой и отправкой чека стоит сеть: если приложение закрыли,
    монеты всё равно должны начислиться — Apple сообщает о той же покупке в
    вебхук, а `appAccountToken` говорит, чья она."""
    from app.routers.webhooks import _bind_by_account_token
    db, user = buyer
    token = user.id.removeprefix("usr_")
    dashed = f"{token[:8]}-{token[8:12]}-{token[12:16]}-{token[16:20]}-{token[20:]}"
    transaction = _purchase("week_6.99",
                            at=datetime.now(timezone.utc) - timedelta(minutes=5),
                            until=datetime.now(timezone.utc) + timedelta(days=7),
                            transaction=f"w-{user.id[-12:]}")
    transaction["appAccountToken"] = dashed.upper()

    row = await _bind_by_account_token(db, transaction, status="active")
    assert row is not None and row.user_id == user.id
    await wallet.ensure_subscription_quota(db, user.id)
    assert (await wallet.get_balance(db, user.id)).available == \
        settings.signup_toontoon_balance + 700


@pytest.mark.asyncio
async def test_webhook_ignores_unknown_or_broken_token(buyer) -> None:
    from app.routers.webhooks import _bind_by_account_token
    db, user = buyer
    base = _purchase("week_6.99", at=datetime.now(timezone.utc),
                     until=datetime.now(timezone.utc) + timedelta(days=7),
                     transaction=f"x-{user.id[-12:]}")
    for token in (None, "", "not-a-uuid", "'; drop table users; --",
                  "00000000-0000-0000-0000-000000000000"):
        transaction = dict(base)
        if token is not None:
            transaction["appAccountToken"] = token
        assert await _bind_by_account_token(db, transaction, status="active") is None
    assert (await wallet.get_balance(db, user.id)).available == settings.signup_toontoon_balance
