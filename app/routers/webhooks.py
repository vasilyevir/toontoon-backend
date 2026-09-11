"""Уведомления App Store: продление, отмена, возврат.

Чек о покупке говорит, что человек заплатил. Но подписка живёт дальше сама:
продлевается, отменяется, возвращается. Об этом Apple сообщает сюда, и другого
способа узнать нет — приложение об отмене не знает, а человек, которому вернули
деньги, обратно не придёт.

Самое дорогое здесь — возврат. Без обработчика подписка оставалась бы активной
после того, как деньги ушли обратно: мы продолжали бы отдавать оплаченное,
перестав получать оплату.

Ручка открыта наружу без ключа приложения (`/api/webhooks` в списке исключений):
её зовёт Apple, а не наше приложение. Единственное, что делает её нашей, —
подпись: цепочка сертификатов до приложенного к коду корня Apple.
"""
from __future__ import annotations

import logging

import hmac

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import models as m
from app.db.repositories import subscriptions as subscriptions_repo
from app.db.repositories import users as users_repo
from app.db.session import get_session as get_db_session
from app.config import settings
from app.services import app_store, wallet

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


class AppStoreNotice(BaseModel):
    """Ровно то, что шлёт Apple: одно поле с подписанным телом."""

    signedPayload: str = Field(min_length=32, max_length=32768)  # noqa: N815


@router.post("/app-store", status_code=status.HTTP_200_OK)
async def app_store_notification(
    body: AppStoreNotice,
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Принять уведомление, применить его к подписке и подтвердить приём.

    Отвечаем `200` на всё, что удалось разобрать, — включая уведомления о
    покупках, которых мы не знаем. Apple повторяет доставку, пока не получит
    `200`, и отвечать ошибкой на то, что мы не собираемся обрабатывать, значит
    просить повторять это вечно.

    А вот неразобранное — `400`: если подпись не сходится, это не уведомление
    Apple, и подтверждать нам нечего.
    """
    try:
        notice = app_store.verify_notification(body.signedPayload)
    except app_store.BadTransaction as exc:
        logger.warning("Уведомление App Store не прошло проверку: %s", exc)
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail="Notification could not be verified") from exc

    # Запись в журнал — она же защита от повторов: идентификатор доставки стоит
    # первичным ключом, поэтому повтор превращается в конфликт вставки, а не во
    # второе применение. Возврат, применённый дважды, был бы безвреден, а вот
    # грант — нет, и полагаться тут на безвредность не стоит.
    inserted = await db.execute(
        insert(m.AppStoreNotification)
        .values(notification_uuid=notice["uuid"], type=notice["type"],
                subtype=notice["subtype"], payload=notice["transaction"])
        .on_conflict_do_nothing(index_elements=[m.AppStoreNotification.notification_uuid])
        .returning(m.AppStoreNotification.notification_uuid)
    )
    if inserted.scalar_one_or_none() is None:
        logger.info("Уведомление %s уже применяли — повтор доставки", notice["uuid"])
        return {"status": "duplicate"}

    new_status = subscriptions_repo.status_for(notice["type"], notice["subtype"])
    if new_status is None:
        logger.info("Уведомление %s (%s) на состояние подписки не влияет",
                    notice["type"], notice["subtype"])
        return {"status": "ignored"}

    row = await subscriptions_repo.apply_notification(
        db, transaction=notice["transaction"], status=new_status)
    bound = False
    if row is None:
        # Покупка, о которой приложение нам не рассказало.
        #
        # Обычно чек приносит оно: купил — отправил — начислили. Но между
        # оплатой и отправкой стоит сеть и живой телефон, и если приложение
        # закрыли или связь пропала, деньги списаны, а монет нет — до
        # следующего запуска. Apple сообщает о той же покупке сюда, и здесь
        # человека можно узнать без приложения: при покупке мы передаём
        # `appAccountToken` — это наш же идентификатор (Илья, 2026-09-10).
        row = await _bind_by_account_token(db, notice["transaction"], status=new_status)
        bound = row is not None
    if row is None:
        logger.info("Уведомление о покупке %s, которой мы не знаем",
                    notice["transaction"].get("originalTransactionId"))
        return {"status": "unknown-purchase"}

    # Монеты — здесь же, не дожидаясь, когда человек откроет приложение.
    # Пополнение идемпотентно по номеру периода, поэтому повтор безвреден.
    await wallet.ensure_subscription_quota(db, row.user_id)

    logger.info("Подписка %s стала %s по уведомлению %s%s",
                row.id, new_status, notice["type"], " (привязана по токену)" if bound else "")
    return {"status": "bound" if bound else "applied", "subscription": new_status}


async def _bind_by_account_token(
    db: AsyncSession, transaction: dict, *, status: str
) -> m.Subscription | None:
    """Найти человека по `appAccountToken` и закрепить за ним покупку.

    Токен — это наш `usr_…` в виде UUID: те же тридцать два знака. Обратное
    преобразование здесь и делается. Чужой аккаунт так не занять: токен
    приходит из подписанного Apple чека, а не от того, кто стучится.
    """
    token = transaction.get("appAccountToken")
    if not token:
        return None
    user_id = "usr_" + str(token).replace("-", "").lower()
    if not users_repo.CLIENT_ID.match(user_id):
        return None
    user = await users_repo.get(db, user_id)
    if user is None:
        logger.warning("В покупке токен %s, а человека с таким идентификатором нет", token)
        return None
    row = await subscriptions_repo.bind(db, user_id=user.id, payload=transaction)
    if status != row.status:
        row.status = status
        await db.flush()
    return row


# ─── Adapty ──────────────────────────────────────────────────────────────────

#: Что событие Adapty говорит о состоянии подписки. Молчащие события (смена
#: тарифа, тестовые) состояние не трогают — для них `None`.
_ADAPTY_STATUS: dict[str, str] = {
    "subscription_started": "active",
    "subscription_renewed": "active",
    "subscription_renewal_reactivated": "active",
    "non_subscription_purchase": "active",
    # Автопродление выключили — оплаченный период дожить обязан.
    "subscription_renewal_cancelled": "active",
    "entered_grace_period": "grace",
    "billing_issue_detected": "grace",
    "subscription_expired": "expired",
    "subscription_paused": "expired",
    "subscription_refunded": "refunded",
    "trial_expired": "expired",
}


@router.post("/adapty", status_code=status.HTTP_200_OK)
async def adapty_event(
    request: Request,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """События подписки от Adapty.

    Второй путь к тем же деньгам. Первый — чек от приложения и уведомления
    Apple напрямую; он и остаётся главным, потому что не зависит ни от кого,
    кроме Apple. Adapty полезен там, где первый молчит: покупка из другого
    магазина в будущем, продление, о котором Apple сообщила, а мы не успели
    принять, — и просто как второе плечо (Илья, 2026-09-11).

    Дублирование безопасно: подписка ищется по номеру исходной транзакции, а
    пополнение квоты идемпотентно по номеру периода. Одно и то же событие,
    доставленное дважды, не даёт вторых монет.

    Отвечаем `200` на всё, что разобрали, — включая события про людей, которых
    мы не знаем. Adapty повторяет доставку девять раз в сутки на любой ответ
    вне диапазона 200–404, и просить его повторять то, что мы не собираемся
    обрабатывать, значит держать очередь мусором.
    """
    secret = settings.adapty_webhook_secret.strip()
    if not secret:
        # Секрет не задан — ручка закрыта. Принимать события о деньгах без
        # проверки нельзя: любой знающий адрес выдал бы себе подписку.
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Webhook is not configured")
    if not hmac.compare_digest((authorization or "").strip(), secret):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Bad authorization")

    body = await request.json()
    if not isinstance(body, dict):
        return {"status": "ignored"}

    event_type = str(body.get("event_type") or "")
    # Проверочный запрос из панели Adapty приходит без события: ему нужен
    # только ответ с валидным JSON.
    if not event_type:
        return {"status": "ok"}

    new_status = _ADAPTY_STATUS.get(event_type)
    if new_status is None:
        logger.info("Событие Adapty %s состояния подписки не меняет", event_type)
        return {"status": "ignored"}

    props = body.get("event_properties") or {}
    row = await _adapty_subscription(db, body, props, status=new_status)
    if row is None:
        return {"status": "unknown-user"}

    await wallet.ensure_subscription_quota(db, row.user_id)
    logger.info("Adapty: подписка %s стала %s по событию %s",
                row.id, new_status, event_type)
    return {"status": "applied", "subscription": new_status}


async def _adapty_subscription(
    db: AsyncSession, body: dict, props: dict, *, status: str
) -> m.Subscription | None:
    """Найти или завести подписку по событию Adapty."""
    original = str(props.get("original_transaction_id")
                   or props.get("transaction_id") or "").strip()
    if not original:
        return None

    known = await subscriptions_repo.get_by_original_transaction(db, original)
    if known is not None:
        if known.status != status:
            known.status = status
            await db.flush()
        return known

    # Покупки ещё нет: заводим её тому, кого назвал Adapty. Идентификатор наш
    # же — мы сами передали его как customer user id.
    user_id = str(body.get("customer_user_id") or "").strip()
    if not users_repo.CLIENT_ID.match(user_id) or await users_repo.get(db, user_id) is None:
        logger.warning("Событие Adapty про неизвестного человека: %s", user_id or "—")
        return None

    payload = {
        "originalTransactionId": original,
        "transactionId": str(props.get("transaction_id") or original),
        "productId": str(props.get("vendor_product_id") or ""),
        "purchaseDate": props.get("purchase_date") or body.get("event_datetime"),
        "expiresDate": props.get("expires_date"),
        "environment": (props.get("environment") or body.get("environment") or "").lower() or None,
    }
    row = await subscriptions_repo.bind(db, user_id=user_id, payload=payload)
    if row.status != status:
        row.status = status
        await db.flush()
    return row
