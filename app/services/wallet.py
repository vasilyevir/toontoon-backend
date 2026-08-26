"""Wallet — two-phase payment for a generation, backed by PostgreSQL.

The shape callers see is unchanged (reserve → confirm | cancel), but underneath
it is now the ledger rather than a number on a Redis object: every charge and
every refund is a row, and the balance is their sum.

Boostyfi is gone from this path — the mobile product buys through the App Store
(CH-17), so the grant/vesting branch has no user left.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.security import new_id
from app.db.repositories import generations as generations_repo
from app.db.repositories import wallet as wallet_repo
from app.models.payment import Balance, Payment, PaymentStatus

logger = logging.getLogger("toontoon.wallet")

InsufficientFunds = wallet_repo.InsufficientFunds


async def get_balance(db: AsyncSession, user_id: str) -> Balance:
    """One number for the UI: the subscription quota plus the free bucket.

    The split is only shown on the upgrade screen — a home screen that displays
    two balances turns into bookkeeping (CH-15).
    """
    b = await wallet_repo.balance(db, user_id)
    return Balance(available=b.total, locked=0, grant_cap=0, grant_expires_at=None)


async def reserve(
    db: AsyncSession, user_id: str, *, amount: int, reason: str = "generation"
) -> Payment:
    """Charge up-front, before the provider is called.

    Charging first and refunding on failure is deliberate: the alternative —
    generate, then charge — hands out free work whenever the charge fails, and
    a generation that already cost us money is the worst thing to give away.
    """
    payment_id = new_id("pay_")
    try:
        await wallet_repo.spend(
            db,
            user_id,
            cost=amount,
            reason=reason,
            ref_id=payment_id,
            idempotency_key=f"spend:{payment_id}",
        )
    except wallet_repo.InsufficientFunds:
        raise
    return Payment(payment_id=payment_id, status=PaymentStatus.PENDING, amount=amount)


async def confirm(db: AsyncSession, payment: Payment) -> None:
    """Nothing to settle: the tokens left the wallet at reserve time.

    Kept so the call sites keep reading as a two-phase flow, and so a provider
    that ever needs a real capture step has a place to put it.
    """
    return None


async def cancel(db: AsyncSession, user_id: str, payment: Payment) -> None:
    """Give the tokens back after a failed generation.

    Refunds land in the free bucket. Returning them to the subscription bucket
    could push it above the plan maximum, and the weekly reset would silently
    eat the difference — the person would be refunded into a bucket that is
    about to be wiped.
    """
    if payment.amount <= 0:
        return
    await wallet_repo.refund(
        db,
        user_id,
        amount=payment.amount,
        bucket="free",
        ref_id=payment.payment_id,
        idempotency_key=f"refund:{payment.payment_id}",
    )


async def settle_owed(db: AsyncSession, *, limit: int = 200) -> list[dict]:
    """Расплатиться по всему, за что деньги взяты, а работы человек не получил.

    Два разных несчастья, и второе я сперва пропустил:

      1. Задача дошла до своего `except`, но вернуть не смогла — база лежала
         ровно в тот момент. Работа помечена `failed`, возврата в книге нет.
      2. Задачу убили раньше, чем она успела что-либо пометить: выкатка, OOM,
         перезапуск пода. Работа осталась `running` навсегда, и первая проверка
         её не видит — она смотрит на `failed`.

    Сеть подстраховки, а не основной путь: обычно задача возвращает сама,
    сразу и в той же транзакции.

    Повторить безопасно: проводка идемпотентна по `refund:{payment_id}`, и
    ключ здесь тот же самый, что у задачи. Значит эта сверка не может заплатить
    дважды, даже если запустить её одновременно в двух местах.

    Возвращает то, что доплатила, — не число, а список: «вернули 4 TOONTOON
    двоим» это разговор с людьми, которым не повезло, и знать надо кому.
    """
    # Сперва признать оборвавшиеся неудачей. Работа, чей процесс убили между
    # «начал» и «пометил», остаётся в `running` навсегда: сама она уже ничего
    # не пометит, и запрос ниже — который смотрит на `failed` — её не увидит.
    # Деньги за неё списаны так же, как за любую другую.
    stale = await generations_repo.fail_stale(
        db, older_than=timedelta(minutes=settings.stale_generation_minutes))
    if stale:
        logger.warning("Сверка признала оборвавшимися %d работ(ы): %s", len(stale),
                       ", ".join(g.id for g in stale))

    owed = await wallet_repo.owed_refunds(db, limit=limit)
    settled = []
    for row in owed:
        try:
            await wallet_repo.refund(
                db,
                row["user_id"],
                amount=row["amount"],
                bucket="free",
                ref_id=row["payment_id"],
                idempotency_key=f"refund:{row['payment_id']}",
            )
        except Exception:  # noqa: BLE001 — один невернувшийся не должен ронять остальных
            logger.exception("Не удалось доплатить за работу %s", row["generation_id"])
            continue
        settled.append(row)
    if settled:
        logger.warning("Сверка вернула деньги за %d работ(ы): %s", len(settled),
                       ", ".join(r["generation_id"] for r in settled))
    return settled


async def daily_reward(db: AsyncSession, user_id: str) -> tuple[Balance, int]:
    """Claim today's reward. Returns the balance and how much was granted."""
    b, granted = await wallet_repo.claim_daily_reward(db, user_id)
    return Balance(available=b.total, locked=0, grant_cap=0, grant_expires_at=None), granted
