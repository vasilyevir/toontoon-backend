"""Квота подписки должна доходить до подписчика.

Она не доходила. Покупка привязывала подписку и запоминала дату отсчёта, тариф
лежал в базе со своей квотой, `ensure_weekly_quota` была написана и покрыта
тестами — а между ними не стояло ничего. Платящий подписчик получал ровно то
же, что бесплатный.

Тесты у той функции были, и все проходили: они звали её напрямую. Отсутствие
вызова так не поймать — поэтому здесь проверяется публичный путь, тот самый,
которым баланс спрашивает приложение.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import delete

from app.db.session import connect, disconnect, get_factory
from app.db import models as m
from app.services import wallet


@pytest_asyncio.fixture
async def buyer():
    """Человек с действующей недельной подпиской."""
    await connect()
    async with get_factory()() as session:
        user = m.User(kind="guest")
        session.add(user)
        await session.flush()
        sub = m.Subscription(
            user_id=user.id,
            original_transaction_id=f"t-{user.id[-10:]}",
            product_id="week_6.99",
            status="active",
            quota_anchor_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
        session.add(sub)
        await session.flush()
        await session.commit()

        yield session, user, sub

        for table in (m.WalletLedger, m.Subscription):
            await session.execute(delete(table).where(table.user_id == user.id))
        await session.execute(delete(m.User).where(m.User.id == user.id))
        await session.commit()
    await disconnect()


@pytest.mark.asyncio
async def test_a_subscriber_gets_the_quota_of_their_plan(buyer):
    session, user, _ = buyer
    balance = await wallet.get_balance(session, user.id)
    assert balance.available == 300, (
        "недельная квота не доехала: проверьте, что product_id тарифа совпадает "
        "с товаром в App Store"
    )


@pytest.mark.asyncio
async def test_asking_twice_inside_one_week_does_not_double_it(buyer):
    """Пополнение висит на чтении баланса, а его спрашивают перед каждым шагом."""
    session, user, _ = buyer
    first = (await wallet.get_balance(session, user.id)).available
    for _ in range(4):
        again = (await wallet.get_balance(session, user.id)).available
    assert again == first, f"баланс вырос от одних только вопросов: {first} → {again}"


@pytest.mark.asyncio
async def test_a_plan_nobody_declared_grants_nothing(buyer):
    """Товар из App Store, которого нет у нас, — повод для записи в журнал, а не
    для щедрости и не для падения чужого запроса."""
    session, user, sub = buyer
    sub.product_id = "something_new"
    await session.flush()

    balance = await wallet.get_balance(session, user.id)
    assert balance.available == 0


@pytest.mark.asyncio
async def test_without_a_subscription_nothing_is_granted(buyer):
    session, user, sub = buyer
    sub.status = "expired"
    await session.flush()

    assert (await wallet.get_balance(session, user.id)).available == 0
