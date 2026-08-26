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

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import models as m
from app.db.repositories import subscriptions as subscriptions_repo
from app.db.session import get_session as get_db_session
from app.services import app_store

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
    if row is None:
        logger.info("Уведомление о покупке %s, которой мы не знаем",
                    notice["transaction"].get("originalTransactionId"))
        return {"status": "unknown-purchase"}

    logger.info("Подписка %s стала %s по уведомлению %s",
                row.id, new_status, notice["type"])
    return {"status": "applied", "subscription": new_status}
