"""Правила кошелька — то, что дороже всего чинить после релиза.

Тесты идут против настоящего PostgreSQL (контейнер toontoon-postgres): половина
проверяемых гарантий — уникальные индексы и транзакции, и на подделке базы они
не проверяются вовсе.

    PYTHONPATH=. .venv/bin/python -m pytest tests -q
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from app.config import settings
from app.db import models as m
from app.db.repositories import wallet as wallet_repo
from app.db.session import connect, disconnect, get_factory

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def db():
    await connect()
    factory = get_factory()
    async with factory() as session:
        user = m.User(kind="guest")
        session.add(user)
        await session.flush()
        await session.commit()
        user_id = user.id

        yield session, user_id

        await session.execute(delete(m.WalletLedger).where(m.WalletLedger.user_id == user_id))
        await session.execute(delete(m.WalletBalance).where(m.WalletBalance.user_id == user_id))
        await session.execute(delete(m.User).where(m.User.id == user_id))
        await session.commit()
    await disconnect()


async def test_grant_is_idempotent(db):
    """Повтор начисления с тем же ключом не должен удваивать деньги."""
    session, uid = db
    await wallet_repo.grant(session, uid, amount=50, bucket="free", reason="signup",
                            idempotency_key=f"signup:{uid}")
    balance = await wallet_repo.grant(session, uid, amount=50, bucket="free", reason="signup",
                                      idempotency_key=f"signup:{uid}")
    assert balance.free == 50


async def test_spend_drains_subscription_first(db):
    """Сначала сгораемая квота, потом бесплатное — иначе недельный сброс
    съедал бы заработанное."""
    session, uid = db
    await wallet_repo.grant(session, uid, amount=100, bucket="free", reason="signup")
    await wallet_repo.reset_subscription_quota(session, uid, quota=850,
                                               idempotency_key=f"q:{uid}:0")

    balance = await wallet_repo.spend(session, uid, cost=150, ref_id="gen_1")
    assert balance.sub == 700
    assert balance.free == 100


async def test_spend_across_buckets_writes_two_entries(db):
    """Списание через границу корзин должно быть видно в журнале построчно."""
    session, uid = db
    await wallet_repo.grant(session, uid, amount=100, bucket="free", reason="signup")
    await wallet_repo.reset_subscription_quota(session, uid, quota=20,
                                               idempotency_key=f"q:{uid}:0")

    balance = await wallet_repo.spend(session, uid, cost=50, ref_id="gen_cross")
    assert (balance.sub, balance.free) == (0, 70)

    from sqlalchemy import select

    rows = (await session.scalars(
        select(m.WalletLedger).where(m.WalletLedger.ref_id == "gen_cross")
    )).all()
    assert sorted((r.bucket, r.delta) for r in rows) == [("free", -30), ("sub", -20)]


async def test_spend_refuses_to_go_negative(db):
    session, uid = db
    await wallet_repo.grant(session, uid, amount=10, bucket="free", reason="signup")
    with pytest.raises(wallet_repo.InsufficientFunds):
        await wallet_repo.spend(session, uid, cost=11)


async def test_weekly_quota_sets_maximum_not_adds(db):
    """Тариф на 850 означает 850 в начале периода, а не 850 сверх остатка."""
    session, uid = db
    anchor = datetime(2026, 8, 1, tzinfo=timezone.utc)
    await wallet_repo.ensure_weekly_quota(session, uid, quota=850, anchor=anchor,
                                          now=anchor + timedelta(days=1))
    await wallet_repo.spend(session, uid, cost=800)

    balance = await wallet_repo.ensure_weekly_quota(session, uid, quota=850, anchor=anchor,
                                                    now=anchor + timedelta(days=8))
    assert balance.sub == 850


async def test_weekly_quota_is_idempotent_within_period(db):
    """Ручку можно звать на каждом запросе — второй вызов в том же периоде
    ничего не делает."""
    session, uid = db
    anchor = datetime(2026, 8, 1, tzinfo=timezone.utc)
    now = anchor + timedelta(days=2)
    await wallet_repo.ensure_weekly_quota(session, uid, quota=850, anchor=anchor, now=now)
    await wallet_repo.spend(session, uid, cost=100)
    balance = await wallet_repo.ensure_weekly_quota(session, uid, quota=850, anchor=anchor, now=now)
    assert balance.sub == 750


async def test_daily_rewards_ladder_gives_hundred_a_week(db):
    """10+10+10+10+10+20+30 = 100 — ровно недельная квота бесплатного тарифа."""
    session, uid = db
    start = date(2026, 8, 1)
    granted = []
    for i in range(7):
        _, amount = await wallet_repo.claim_daily_reward(session, uid, today=start + timedelta(days=i))
        granted.append(amount)
    assert granted == [10, 10, 10, 10, 10, 20, 30]
    assert sum(granted) == settings.free_weekly_quota


async def test_daily_reward_twice_a_day_grants_nothing(db):
    session, uid = db
    today = date(2026, 8, 1)
    await wallet_repo.claim_daily_reward(session, uid, today=today)
    _, second = await wallet_repo.claim_daily_reward(session, uid, today=today)
    assert second == 0


async def test_missed_day_restarts_the_ladder(db):
    session, uid = db
    start = date(2026, 8, 1)
    for i in range(3):
        await wallet_repo.claim_daily_reward(session, uid, today=start + timedelta(days=i))
    _, after_gap = await wallet_repo.claim_daily_reward(session, uid, today=start + timedelta(days=10))
    assert after_gap == 10


async def test_reward_is_trimmed_at_the_cap_not_refused(db):
    """У потолка награда обрезается: стрик не должен ломаться из-за полного
    кошелька."""
    session, uid = db
    wallet = await wallet_repo.ensure(session, uid)
    wallet.free_balance = settings.free_balance_cap - 4
    wallet.free_cap = settings.free_balance_cap
    await session.flush()

    balance, granted = await wallet_repo.claim_daily_reward(session, uid, today=date(2026, 8, 1))
    assert granted == 4
    assert balance.free == settings.free_balance_cap


async def test_refund_is_a_separate_entry(db):
    """Возврат — отдельная строка журнала, а не тихая правка баланса."""
    session, uid = db
    await wallet_repo.grant(session, uid, amount=50, bucket="free", reason="signup")
    await wallet_repo.spend(session, uid, cost=10, ref_id="pay_1", idempotency_key="spend:pay_1")
    balance = await wallet_repo.refund(session, uid, amount=10, ref_id="pay_1",
                                       idempotency_key="refund:pay_1")
    assert balance.free == 50

    from sqlalchemy import select

    reasons = (await session.scalars(
        select(m.WalletLedger.reason).where(m.WalletLedger.user_id == uid)
    )).all()
    assert "refund" in reasons


async def test_balance_equals_sum_of_ledger(db):
    """Главная гарантия: баланс восстановим из журнала."""
    session, uid = db
    await wallet_repo.grant(session, uid, amount=50, bucket="free", reason="signup")
    await wallet_repo.reset_subscription_quota(session, uid, quota=850, idempotency_key=f"q:{uid}")
    await wallet_repo.spend(session, uid, cost=900, ref_id="gen_big")

    from sqlalchemy import func, select

    total = await session.scalar(
        select(func.sum(m.WalletLedger.delta)).where(m.WalletLedger.user_id == uid)
    )
    balance = await wallet_repo.balance(session, uid)
    assert total == balance.total


async def test_guest_balance_transfers_once_per_account(db):
    """Перенос гостевого баланса — один раз за жизнь аккаунта.

    Первый гость приносит своё, второй не приносит ничего: ключ идемпотентности
    привязан к аккаунту, а не к гостю, поэтому фарм наград на новых гостевых
    сессиях ничего не даёт.
    """
    session, account_id = db
    await wallet_repo.grant(session, account_id, amount=50, bucket="free", reason="signup")

    guests = []
    for _ in range(2):
        guest = m.User(kind="guest")
        session.add(guest)
        await session.flush()
        await wallet_repo.grant(session, guest.id, amount=59, bucket="free", reason="signup")
        guests.append(guest.id)

    first = await wallet_repo.transfer_guest_balance(session, guest_id=guests[0], target_id=account_id)
    second = await wallet_repo.transfer_guest_balance(session, guest_id=guests[1], target_id=account_id)

    balance = await wallet_repo.balance(session, account_id)
    assert (first, second) == (59, 0)
    assert balance.free == 109

    # Гость, чей баланс переехал, обнулён — журнал остаётся сходящимся.
    donor = await wallet_repo.balance(session, guests[0])
    assert donor.free == 0

    for guest_id in guests:
        await session.execute(delete(m.WalletLedger).where(m.WalletLedger.user_id == guest_id))
        await session.execute(delete(m.WalletBalance).where(m.WalletBalance.user_id == guest_id))
        await session.execute(delete(m.User).where(m.User.id == guest_id))


async def test_guest_transfer_is_trimmed_by_cap(db):
    """Перенос не может пробить потолок бесплатной корзины."""
    session, account_id = db
    await wallet_repo.grant(session, account_id, amount=settings.free_balance_cap - 10,
                            bucket="free", reason="signup")
    guest = m.User(kind="guest")
    session.add(guest)
    await session.flush()
    await wallet_repo.grant(session, guest.id, amount=100, bucket="free", reason="signup")

    carried = await wallet_repo.transfer_guest_balance(session, guest_id=guest.id, target_id=account_id)
    balance = await wallet_repo.balance(session, account_id)
    assert carried == 10
    assert balance.free == settings.free_balance_cap

    await session.execute(delete(m.WalletLedger).where(m.WalletLedger.user_id == guest.id))
    await session.execute(delete(m.WalletBalance).where(m.WalletBalance.user_id == guest.id))
    await session.execute(delete(m.User).where(m.User.id == guest.id))


async def test_concurrent_spends_cannot_outspend_the_balance():
    """Пять одновременных списаний на балансе, которого хватает на одно.

    Это тот самый случай, который аудит нашёл живым: остаток читался,
    проверялся и записывался абсолютным значением без блокировки строки, и
    пять запросов, пришедших разом, получали пять кадров по цене одного.
    Журнал после этого показывал −60 при балансе 0, то есть переставал
    сходиться с остатком — а на нём держится вся разборка жалоб про деньги.

    Тест идёт против настоящего PostgreSQL и своими сессиями на каждое
    списание: у каждой своя транзакция, как у пяти параллельных запросов.
    На поддельной базе он не проверяет ничего — блокировки там нет.

    Соединения открываются ЗАРАНЕЕ, и это не украшение. На холодном пуле
    задачи выстраиваются в очередь за подключением, проходит одна, и тест
    зеленеет даже на сломанном коде — именно так первая проба аудита едва не
    объявила дыру несуществующей.
    """
    from sqlalchemy import text

    await connect()
    factory = get_factory()
    async with factory() as setup:
        user = m.User(kind="guest")
        setup.add(user)
        await setup.flush()
        uid = user.id
        await wallet_repo.grant(setup, uid, amount=15, bucket="free", reason="signup",
                                idempotency_key=f"race:{uid}")
        await setup.commit()

    sessions = [factory() for _ in range(5)]
    for s in sessions:
        await s.execute(text("SELECT 1"))  # соединение в руках до старта

    async def spend_one(s, tag):
        try:
            await wallet_repo.spend(s, uid, cost=15, reason="generation",
                                    ref_id=f"pay_{tag}", idempotency_key=f"spend:pay_{tag}")
            await s.commit()
            return True
        except wallet_repo.InsufficientFunds:
            await s.rollback()
            return False

    go = asyncio.Event()

    async def worker(s, tag):
        await go.wait()
        return await spend_one(s, tag)

    tasks = [asyncio.create_task(worker(sessions[i], f"{uid}_{i}")) for i in range(5)]
    await asyncio.sleep(0.05)
    go.set()
    results = await asyncio.gather(*tasks)
    for s in sessions:
        await s.close()

    async with factory() as check:
        balance = await wallet_repo.balance(check, uid)
        rows = (await check.scalars(
            select(m.WalletLedger).where(m.WalletLedger.user_id == uid))).all()

        assert sum(results) == 1, (
            f"пятнадцати монет хватает на один кадр, а списаний прошло {sum(results)}"
        )
        assert balance.free == 0
        # Главная гарантия файла: остаток — это сумма журнала. Гонка ломала
        # именно её, и проверять надо её, а не только число списаний.
        assert sum(r.delta for r in rows) == balance.total

        await check.execute(delete(m.WalletLedger).where(m.WalletLedger.user_id == uid))
        await check.execute(delete(m.WalletBalance).where(m.WalletBalance.user_id == uid))
        await check.execute(delete(m.User).where(m.User.id == uid))
        await check.commit()
    await disconnect()
