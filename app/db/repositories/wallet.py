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
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
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


async def ensure_weekly_quota(
    session: AsyncSession,
    user_id: str,
    *,
    quota: int,
    anchor: datetime,
    now: Optional[datetime] = None,
) -> Balance:
    """Refill the subscription bucket if a new 7-day period has started.

    The period is counted from the purchase date, not from App Store renewals:
    a monthly plan has four-and-a-bit resets inside one billing period, and a
    "week" that drifted with renewals would be impossible to explain to anyone.

    Idempotent by period index, so calling this on every request is safe — the
    first call in a period resets, the rest do nothing.
    """
    now = now or datetime.now(timezone.utc)
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=timezone.utc)
    if now < anchor:
        return await balance(session, user_id)

    period = (now - anchor).days // 7
    key = f"quota:{user_id}:{anchor.date().isoformat()}:{period}"
    if await _already_applied(session, key):
        return await balance(session, user_id)

    result = await reset_subscription_quota(session, user_id, quota=quota, idempotency_key=key)
    wallet = await ensure(session, user_id)
    wallet.sub_period_start = anchor + timedelta(days=7 * period)
    wallet.sub_period_end = anchor + timedelta(days=7 * (period + 1))
    await session.flush()
    return result


async def claim_daily_reward(
    session: AsyncSession, user_id: str, *, today: Optional[date] = None
) -> tuple[Balance, int]:
    """Hand out the daily reward and return ``(balance, amount_granted)``.

    The ladder rises through the week — 10 for the first five days, 20 on the
    sixth, 30 on the seventh — which is what makes coming back tomorrow worth
    more than coming back next week. A missed day resets the streak to the
    beginning.

    The date is the **server's**, never the device's: otherwise moving the clock
    forward would be an infinite supply of tokens.
    """
    schedule = settings.daily_reward_list
    if not schedule:
        return await balance(session, user_id), 0

    today = today or datetime.now(timezone.utc).date()
    wallet = await ensure(session, user_id)

    if wallet.last_reward_date == today:
        return Balance(free=wallet.free_balance, sub=wallet.sub_balance), 0

    consecutive = wallet.last_reward_date == today - timedelta(days=1)
    streak = (wallet.reward_streak + 1) if consecutive else 1
    if streak > len(schedule):
        streak = 1  # the ladder starts over on the next week
    amount = schedule[streak - 1]

    # The cap is a ceiling, not a wall: a reward that would overflow it is
    # trimmed rather than refused, so the streak is not silently broken.
    cap = wallet.free_cap if wallet.free_cap is not None else settings.free_balance_cap
    room = max(0, cap - wallet.free_balance)
    granted = min(amount, room)

    wallet.last_reward_date = today
    wallet.reward_streak = streak
    if granted:
        wallet.free_balance += granted
        session.add(
            m.WalletLedger(
                user_id=user_id, bucket="free", delta=granted, reason="daily_reward",
                idempotency_key=f"daily:{user_id}:{today.isoformat()}",
                balance_after=wallet.free_balance,
            )
        )
    await session.flush()
    return Balance(free=wallet.free_balance, sub=wallet.sub_balance), granted


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


async def transfer_guest_balance(
    session: AsyncSession, *, guest_id: str, target_id: str
) -> int:
    """Move a guest's free balance into the account on sign-in — **once per
    account**.

    Losing what you earned before logging in is a bad trade for pressing
    "sign in", so the balance travels with the work. But it travels only the
    first time: the idempotency key is tied to the **account**, not to the
    guest, so a second (third, tenth) guest folded into the same account adds
    nothing. That is what stops "collect rewards on a fresh guest, sign in,
    repeat" from being a strategy.

    The subscription bucket is not transferred: a guest cannot have one.
    """
    already = await _already_applied(session, f"merge:{target_id}")
    if already:
        return 0

    guest_wallet = await session.get(m.WalletBalance, guest_id)
    if guest_wallet is None or guest_wallet.free_balance <= 0:
        return 0

    target_wallet = await ensure(session, target_id)
    cap = target_wallet.free_cap if target_wallet.free_cap is not None else settings.free_balance_cap
    room = max(0, cap - target_wallet.free_balance)
    amount = min(guest_wallet.free_balance, room)
    if amount <= 0:
        return 0

    # Debit the guest as well: a transfer that only credits one side would make
    # the journal stop summing to the balances.
    guest_wallet.free_balance -= amount
    session.add(
        m.WalletLedger(
            user_id=guest_id, bucket="free", delta=-amount, reason="merge",
            ref_id=target_id, idempotency_key=f"merge_out:{guest_id}",
            balance_after=guest_wallet.free_balance,
        )
    )

    target_wallet.free_balance += amount
    session.add(
        m.WalletLedger(
            user_id=target_id, bucket="free", delta=amount, reason="merge",
            ref_id=guest_id, idempotency_key=f"merge:{target_id}",
            balance_after=target_wallet.free_balance,
        )
    )
    await session.flush()
    return amount


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
