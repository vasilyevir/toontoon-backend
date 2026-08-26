"""Покупки: одна на человека, и она же его опознаёт.

`original_transaction_id` уникален глобально — это и есть замок, из-за которого
одна покупка не может кормить два аккаунта. Он же служит именем человека, когда
имени нет: вход из продукта убран, и узнаёт его App Store.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import models as m


def _moment(value) -> Optional[datetime]:
    """Время из чека — миллисекунды с начала эпохи."""
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def _fields(payload: dict) -> dict:
    return {
        "product_id": str(payload.get("productId") or ""),
        "status": "active",
        "current_period_start": _moment(payload.get("purchaseDate")),
        "current_period_end": _moment(payload.get("expiresDate")),
        "environment": str(payload.get("environment") or "").lower() or None,
        "raw_payload": payload,
    }


async def get_by_original_transaction(
    session: AsyncSession, original_transaction_id: str
) -> Optional[m.Subscription]:
    return await session.scalar(
        select(m.Subscription).where(
            m.Subscription.original_transaction_id == original_transaction_id)
    )


async def bind(session: AsyncSession, *, user_id: str, payload: dict) -> m.Subscription:
    """Первое появление покупки: закрепить за этим человеком."""
    row = m.Subscription(
        user_id=user_id,
        original_transaction_id=str(payload["originalTransactionId"]),
        # Отсчёт недельной квоты — от покупки, а не от продления в App Store.
        quota_anchor_at=_moment(payload.get("purchaseDate")) or datetime.now(timezone.utc),
        **_fields(payload),
    )
    session.add(row)
    await session.flush()
    return row


async def refresh(session: AsyncSession, row: m.Subscription, *, payload: dict) -> m.Subscription:
    """Тот же владелец, свежий чек: обновить сроки и состояние."""
    for field, value in _fields(payload).items():
        setattr(row, field, value)
    await session.flush()
    return row


async def rebind(
    session: AsyncSession, row: m.Subscription, *, user_id: str, payload: dict
) -> m.Subscription:
    """Владелец удалён — покупка снова ничья и достаётся тому, кто её принёс."""
    row.user_id = user_id
    return await refresh(session, row, payload=payload)


async def active_for_user(session: AsyncSession, user_id: str) -> Optional[m.Subscription]:
    return await session.scalar(
        select(m.Subscription)
        .where(m.Subscription.user_id == user_id, m.Subscription.status == "active")
        .order_by(m.Subscription.created_at.desc())
    )
