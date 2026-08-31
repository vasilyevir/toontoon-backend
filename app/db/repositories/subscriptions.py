"""Покупки: одна на человека, и она же его опознаёт.

`original_transaction_id` уникален глобально — это и есть замок, из-за которого
одна покупка не может кормить два аккаунта. Он же служит именем человека, когда
имени нет: вход из продукта убран, и узнаёт его App Store.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, or_, select
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
    """Что чек рассказывает о себе. Без состояния — его решает не наличие чека.

    Раньше здесь стояло `"status": "active"` жёстко, и это была дыра: чек
    предъявляется при каждом запуске приложения, а `refresh` раскладывал эти
    поля по строке подписки. Значит человек, которому вернули деньги, показывал
    сохранённую строку JWS ещё раз — и подписка снова становилась действующей.
    Отозвать чек нельзя, он у него на руках навсегда.
    """
    return {
        "product_id": str(payload.get("productId") or ""),
        "current_period_start": _moment(payload.get("purchaseDate")),
        "current_period_end": _moment(payload.get("expiresDate")),
        "environment": str(payload.get("environment") or "").lower() or None,
        "raw_payload": payload,
    }


def status_from_receipt(payload: dict, *, now: Optional[datetime] = None) -> str:
    """Что чек говорит о своей покупке сам.

    Три состояния, и все три написаны в самом чеке:

    * `revocationDate` — деньги вернули, Apple помечает это прямо в транзакции;
    * `expiresDate` в прошлом — срок вышел;
    * иначе действует.

    Раньше не проверялось ни то, ни другое: `current_period_end` писался в базу
    и не читался нигде во всём коде, поэтому чек из августа 2025-го давал
    действующую подписку год спустя.
    """
    now = now or datetime.now(timezone.utc)
    if payload.get("revocationDate") or payload.get("revocationReason") is not None:
        return "refunded"
    ends = _moment(payload.get("expiresDate"))
    if ends is not None and ends <= now:
        return "expired"
    return "active"


def _tells_us_something_new(payload: dict, row: m.Subscription) -> bool:
    """Новее ли этот чек того, что уже записано.

    Вопрос ровно один: продлил ли человек покупку с прошлого раза. Если срок в
    чеке не дальше того, что у нас записано, — это повтор уже известного, и
    трогать по нему состояние нельзя: именно так возврат и отменялся.

    А вот честное продление или новая подписка после возврата приносят срок
    дальше прежнего — и такой чек законно возвращает человеку доступ. Подделать
    его нельзя: тело подписано Apple.
    """
    fresh = _moment(payload.get("expiresDate")) or _moment(payload.get("purchaseDate"))
    if fresh is None:
        return False
    known = row.current_period_end or row.current_period_start
    return known is None or fresh > known


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
        status=status_from_receipt(payload),
        **_fields(payload),
    )
    session.add(row)
    await session.flush()
    return row


async def refresh(
    session: AsyncSession, row: m.Subscription, *, payload: dict,
    now: Optional[datetime] = None,
) -> m.Subscription:
    """Тот же владелец предъявил чек: обновить сроки, если он новее.

    Предъявление — обычное дело, а не событие: приложение показывает чек при
    каждом запуске, чтобы узнать человека. Значит эта функция обязана уметь
    отличать «продлил» от «показал то же самое», иначе повтор старой строки
    отменяет возврат денег.

    Повтор известного не меняет ничего — даже сроков: они уже записаны.
    """
    if not _tells_us_something_new(payload, row):
        return row
    for field, value in _fields(payload).items():
        setattr(row, field, value)
    row.status = status_from_receipt(payload, now=now)
    await session.flush()
    return row


async def rebind(
    session: AsyncSession, row: m.Subscription, *, user_id: str, payload: dict
) -> m.Subscription:
    """Владелец удалён — покупка снова ничья и достаётся тому, кто её принёс."""
    row.user_id = user_id
    await refresh(session, row, payload=payload)
    # Свой flush: чек мог оказаться не новее, и тогда `refresh` ничего не
    # записал — а смену владельца сохранить надо в любом случае.
    await session.flush()
    return row


async def active_for_user(session: AsyncSession, user_id: str) -> Optional[m.Subscription]:
    """Действующая подписка — и «действующая» значит в том числе «не истёкшая».

    Одного `status` мало: он ставится по чеку и по уведомлениям, а между
    уведомлениями срок кончается сам. Пока проверки не было,
    `current_period_end` писался в базу и не читался никем, и просроченная
    подписка оставалась действующей вечно.

    Платящих это не запирает: приложение показывает свежий чек при каждом
    запуске (`Purchases.restore` по `currentEntitlements`), и продление
    отодвигает срок раньше, чем человек его заметит.
    """
    return await session.scalar(
        select(m.Subscription)
        .where(
            m.Subscription.user_id == user_id,
            m.Subscription.status == "active",
            or_(
                m.Subscription.current_period_end.is_(None),
                m.Subscription.current_period_end > func.now(),
            ),
        )
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
