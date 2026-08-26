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


# Что каждое уведомление делает с подпиской.
#
# Типы взяты из App Store Server Notifications V2. Здесь только те, что меняют
# ответ на вопрос «действует ли подписка сейчас»; остальные (смена цены,
# продление пробного периода) ложатся в журнал и на состояние не влияют.
#
# `REFUND` — самый важный и самый неприятный: человеку вернули деньги, и
# подписка обязана прекратиться. Без обработчика она оставалась бы активной, то
# есть мы продолжали бы отдавать оплаченное после возврата оплаты.
_STATUS_BY_TYPE: dict[str, str] = {
    "SUBSCRIBED": "active",
    "DID_RENEW": "active",
    "OFFER_REDEEMED": "active",
    # Автопродление выключили — но оплаченный период дожить обязан.
    "DID_CHANGE_RENEWAL_STATUS": "active",
    "DID_CHANGE_RENEWAL_PREF": "active",
    # Платёж не прошёл: Apple ещё пробует, доступ пока остаётся.
    "DID_FAIL_TO_RENEW": "grace",
    "GRACE_PERIOD_EXPIRED": "expired",
    "EXPIRED": "expired",
    "REFUND": "refunded",
    "REVOKE": "refunded",
}


def status_for(notification_type: str, subtype: str | None = None) -> Optional[str]:
    """Каким станет состояние подписки. `None` — уведомление её не трогает."""
    status = _STATUS_BY_TYPE.get(notification_type)
    # «Отменили автопродление» и «передумали отменять» — оба приходят типом
    # DID_CHANGE_RENEWAL_STATUS, и оба оставляют оплаченный период в силе.
    # Прекращает его только EXPIRED, который придёт своим чередом.
    return status


async def apply_notification(
    session: AsyncSession, *, transaction: dict, status: str
) -> Optional[m.Subscription]:
    """Применить состояние к подписке, о которой пришло уведомление.

    `None` — такой покупки мы не знаем. Это законно: уведомления приходят и о
    песочнице, и о покупках, сделанных до того, как приложение научилось их
    привязывать. Молчать в этом случае правильнее, чем заводить подписку без
    владельца.
    """
    row = await get_by_original_transaction(
        session, str(transaction["originalTransactionId"]))
    if row is None:
        return None
    for field, value in _fields(transaction).items():
        setattr(row, field, value)
    # Состояние ставится последним: `_fields` возвращает «active» по факту
    # наличия чека, а решает здесь тип уведомления.
    row.status = status
    await session.flush()
    return row
