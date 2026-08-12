"""Wallet repository — two buckets and an append-only journal.

Rules that live here and nowhere else:

* the balance row and the ledger entry are written **in the same transaction**,
  so a balance can always be re-derived by summing the journal;
* every grant carries an ``idempotency_key``: App Store redelivers
  notifications and networks retry, so applying the same thing twice must be a
  no-op rather than free tokens;
* spending drains the **subscription bucket first**, then the free one — that
  way the weekly reset does not throw away tokens the person earned (CH-17).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import models as m


class InsufficientFunds(RuntimeError):
    """Raised instead of letting a balance go negative."""


@dataclass(slots=True)
class Balance:
    free: int
    sub: int

    @property
    def total(self) -> int:
        return self.free + self.sub


async def ensure(session: AsyncSession, user_id: str) -> m.WalletBalance:
    wallet = await session.get(m.WalletBalance, user_id)
    if wallet is None:
        wallet = m.WalletBalance(user_id=user_id)
        session.add(wallet)
        await session.flush()
    return wallet


async def balance(session: AsyncSession, user_id: str) -> Balance:
    wallet = await session.get(m.WalletBalance, user_id)
    if wallet is None:
        return Balance(free=0, sub=0)
    return Balance(free=wallet.free_balance, sub=wallet.sub_balance)


async def grant(
    session: AsyncSession,
    user_id: str,
    *,
    amount: int,
    bucket: str,
    reason: str,
    idempotency_key: Optional[str] = None,
    ref_id: Optional[str] = None,
) -> Balance:
    """Add tokens. A repeat with the same key changes nothing and does not raise."""
    if amount <= 0:
        raise ValueError("grant amount must be positive")

    if idempotency_key and await _already_applied(session, idempotency_key):
        return await balance(session, user_id)

    wallet = await ensure(session, user_id)
    if bucket == "sub":
        wallet.sub_balance += amount
        after = wallet.sub_balance
    else:
        wallet.free_balance += amount
        after = wallet.free_balance

    session.add(
        m.WalletLedger(
            user_id=user_id,
            bucket=bucket,
            delta=amount,
            reason=reason,
            ref_id=ref_id,
            idempotency_key=idempotency_key,
            balance_after=after,
        )
    )
    try:
        await session.flush()
    except IntegrityError:
        # Lost the race against a concurrent identical grant — the other one won,
        # which is exactly what the unique key is for.
        await session.rollback()
        return await balance(session, user_id)
    return Balance(free=wallet.free_balance, sub=wallet.sub_balance)


async def reset_subscription_quota(
    session: AsyncSession, user_id: str, *, quota: int, idempotency_key: str
) -> Balance:
    """Set the subscription bucket **to** the plan maximum, not add to it.

    A plan of 850 means 850 at the start of every period regardless of what was
    left — that is what "burns" means (CH-17).
    """
    if await _already_applied(session, idempotency_key):
        return await balance(session, user_id)

    wallet = await ensure(session, user_id)
    delta = quota - wallet.sub_balance
    wallet.sub_balance = quota
    session.add(
        m.WalletLedger(
            user_id=user_id,
            bucket="sub",
            delta=delta,
            reason="sub_reset",
            idempotency_key=idempotency_key,
            balance_after=quota,
        )
    )
    await session.flush()
    return Balance(free=wallet.free_balance, sub=wallet.sub_balance)


async def spend(
    session: AsyncSession,
    user_id: str,
    *,
    cost: int,
    reason: str = "generation",
    ref_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> Balance:
    """Charge ``cost`` across both buckets, subscription first.

    A charge that crosses the bucket boundary produces two ledger rows — one per
    bucket — because a journal that hides where the tokens came from cannot be
    used to answer a complaint.
    """
    if cost <= 0:
        return await balance(session, user_id)

    wallet = await ensure(session, user_id)
    if wallet.sub_balance + wallet.free_balance < cost:
        raise InsufficientFunds(f"need {cost}, have {wallet.sub_balance + wallet.free_balance}")

    remaining = cost
    from_sub = min(wallet.sub_balance, remaining)
    if from_sub:
        wallet.sub_balance -= from_sub
        remaining -= from_sub
        session.add(
            m.WalletLedger(
                user_id=user_id, bucket="sub", delta=-from_sub, reason=reason,
                ref_id=ref_id,
                idempotency_key=f"{idempotency_key}:sub" if idempotency_key else None,
                balance_after=wallet.sub_balance,
            )
        )
    if remaining:
        wallet.free_balance -= remaining
        session.add(
            m.WalletLedger(
                user_id=user_id, bucket="free", delta=-remaining, reason=reason,
                ref_id=ref_id,
                idempotency_key=f"{idempotency_key}:free" if idempotency_key else None,
                balance_after=wallet.free_balance,
            )
        )

    await session.flush()
    return Balance(free=wallet.free_balance, sub=wallet.sub_balance)


async def refund(
    session: AsyncSession,
    user_id: str,
    *,
    amount: int,
    bucket: str = "free",
    ref_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> Balance:
    """Give tokens back when a generation fails.

    A separate journal entry, never a quiet edit of the balance — the refund has
    to be visible next to the charge it undoes.
    """
    return await grant(
        session, user_id, amount=amount, bucket=bucket, reason="refund",
        ref_id=ref_id, idempotency_key=idempotency_key,
    )


async def _already_applied(session: AsyncSession, idempotency_key: str) -> bool:
    stmt = select(m.WalletLedger.id).where(m.WalletLedger.idempotency_key == idempotency_key)
    return await session.scalar(stmt) is not None
