"""Generation history.

Backed by PostgreSQL: kept for good, paged by creation time, and deletable —
the Redis list this replaces silently dropped everything past the 200th entry
while the product promised to keep it (CH-13).
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import models as m
from app.db.repositories import generations as generations_repo
from app.db.repositories import media as media_repo
from app.db.session import get_session as get_db_session
from app.deps import Context, required_context

router = APIRouter(prefix="/api", tags=["generations"])


# Внутренняя причина отказа → то, что не стыдно показать человеку.
#
# Наружу нельзя отдавать `row.error` как есть: там имена провайдеров, адреса
# их хранилищ и типы исключений. Это и подсказка тому, кто ищет наши слабые
# места, и бессмыслица для того, кто просто хотел картинку.
#
# Но и молчать нельзя, а молчали мы именно так: приложение получало
# `status: failed` и ничего больше. Человек видел, что не вышло, и не знал ни
# почему, ни вернулись ли монеты. Про монеты вопрос первый, и ответ на него
# входит в каждую строку ниже: за неудачу мы не берём — списание
# откатывается, а если откатить не вышло, доплачивает сверка при следующем
# запуске (`settle_unpaid_refunds`).
#
# Разбор по подстроке, а не по коду ошибки, потому что кода у нас нет: причина
# приходит текстом из разных мест. Совпадений мало и они широкие намеренно —
# лучше показать общую фразу, чем угадать неверную.
_ОТКАЗЫ: tuple[tuple[tuple[str, ...], str], ...] = (
    (("safety", "content policy", "content_policy", "content checker", "не взялась",
      "refus", "blocked", "moderation"),
     "The model wouldn't work with this photo. "
     "Your TOONTOON weren't charged — try a different shot."),
    (("промпт собрать нечем", "translation", "перевод недоступен"),
     "We couldn't put the request together. Your TOONTOON weren't charged."),
    (("all providers failed", "unavailable", "timeout", "readtimeout",
      "insufficient credits", "http 402", "http 5"),
     "Our drawing service didn't answer. "
     "Your TOONTOON weren't charged — try again in a minute."),
)

_ОТКАЗ_ПО_УМОЛЧАНИЮ = (
    "Something went wrong on our side. Your TOONTOON weren't charged."
)


def failure_text(error: Optional[str]) -> str:
    """Человеческая причина отказа. Никогда не возвращает внутренний текст."""
    низом = (error or "").lower()
    for приметы, ответ in _ОТКАЗЫ:
        if any(п in низом for п in приметы):
            return ответ
    return _ОТКАЗ_ПО_УМОЛЧАНИЮ


def _prompt_for_client(row: m.Generation) -> Optional[str]:
    """Что показать человеку как «его просьбу».

    Для работы по стилю из каталога `row.prompt` — это текст стиля, написанный
    руками и уходящий в модель; это продукт, а не просьба человека, и любому
    гостю через историю он не нужен. Показываем то, что человек сказал сам.
    """
    if not row.style_id:
        return row.prompt
    params = row.request_params or {}
    return params.get("refine_note") or params.get("said") or None


def _serialize(row: m.Generation) -> dict:
    """Media is exposed as API links, never as storage paths.

    The client gets an id-stable URL, so a cached image stays valid while the
    object behind it can move between providers.
    """
    result = f"/api/media/{row.result_media_id}" if row.result_media_id else None
    thumb = f"/api/media/{row.result_media_id}?thumb=true" if row.result_media_id else None
    return {
        "id": row.id,
        "type": (row.request_params or {}).get("type", "image"),
        "operation": row.operation,
        "status": row.status,
        "prompt": _prompt_for_client(row),
        "result_url": result,
        "thumbnail_url": thumb,
        "cost": row.cost,
        "style_id": row.style_id,
        "share_id": row.share_id,
        "created_at": row.created_at.isoformat(),
        # Только у неудачных: у остальных поле молчит, чтобы клиенту не
        # приходилось гадать, показывать его или нет.
        "failure": failure_text(row.error) if row.status == "failed" else None,
    }


@router.get("/generations")
async def list_generations(
    ctx: Context = Depends(required_context),
    db: AsyncSession = Depends(get_db_session),
    limit: int = Query(default=30, ge=1, le=100),
    before: Optional[datetime] = Query(default=None, description="Курсор: created_at предыдущей страницы"),
    operation: Optional[str] = Query(default=None),
) -> list[dict]:
    user, _ = ctx
    rows = await generations_repo.list_for_user(
        db, user.id, limit=limit, before=before, operation=operation
    )
    return [_serialize(row) for row in rows]


@router.get("/generations/{gen_id}")
async def get_generation(
    gen_id: str,
    ctx: Context = Depends(required_context),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    user, _ = ctx
    row = await generations_repo.get(db, gen_id, user_id=user.id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found")
    return _serialize(row)


@router.delete("/generations/{gen_id}")
async def delete_generation(
    gen_id: str,
    ctx: Context = Depends(required_context),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Remove a work from history.

    "Delete" means the file is erased from storage and the share link stops
    working — not merely that the card disappears from a screen. The row itself
    survives because the wallet ledger points at it.
    """
    user, _ = ctx
    row = await generations_repo.get(db, gen_id, user_id=user.id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found")

    if row.result_media_id:
        asset = await db.get(m.MediaAsset, row.result_media_id)
        if asset is not None:
            await media_repo.soft_delete(db, asset)
    await generations_repo.soft_delete(db, row)
    return {"ok": True}


@router.post("/generations/{gen_id}/share")
async def share_generation(
    gen_id: str,
    ctx: Context = Depends(required_context),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    user, _ = ctx
    row = await generations_repo.get(db, gen_id, user_id=user.id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found")
    share_id = await generations_repo.ensure_share_id(db, row)
    return {"share_id": share_id, "share_url": f"{settings.frontend_url}/s/{share_id}"}


@router.delete("/generations/{gen_id}/share")
async def unshare_generation(
    gen_id: str,
    ctx: Context = Depends(required_context),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Отозвать публичную ссылку. Работа остаётся, ссылка перестаёт открываться."""
    user, _ = ctx
    row = await generations_repo.get(db, gen_id, user_id=user.id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found")
    row.share_id = None
    await db.flush()
    return {"ok": True}


def _serialize_public(row: m.Generation) -> dict:
    """То, что видит человек по ссылке, — и ничего сверх картинки.

    Внутренний `_serialize` отдаёт промпт, а в промпте по замыслу стоят имена
    из профилей: дети, партнёры. Публичная ссылка на это права не даёт. Ни
    `id` работы, ни цена, ни текст отказа наружу тоже не нужны.
    """
    result = f"/api/media/{row.result_media_id}" if row.result_media_id else None
    thumb = f"/api/media/{row.result_media_id}?thumb=true" if row.result_media_id else None
    return {
        "type": (row.request_params or {}).get("type", "image"),
        "result_url": result,
        "thumbnail_url": thumb,
        "style_id": row.style_id,
        "created_at": row.created_at.isoformat(),
    }


@router.get("/share/{share_id}", tags=["public"])
async def get_shared(share_id: str, db: AsyncSession = Depends(get_db_session)) -> dict:
    """Public view of a deliberately shared work. Everything else stays private."""
    row = await generations_repo.get_by_share_id(db, share_id)
    if row is None or row.status != "done" or not row.result_media_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found")
    return _serialize_public(row)
