"""Профиль человека — набор снимков, по которым он появляется в кадре.

Смысл в том, чтобы не прикладывать своё лицо каждый раз. Один раз собранный
набор подставляется во все генерации, а в разговоре достаточно сказать, про
кого речь.

Пока здесь только разбор набора: он идёт до сборки профиля, а не после, потому
что набор решает всё, что будет дальше. Двадцать кадров в одном свитере у одной
стены дают профиль, который считает свитер и стену частью человека, и заметно
это станет на десятой генерации, когда менять будет поздно.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MediaAsset
from app.db.repositories import profiles as profiles_repo
from app.db.repositories import subscriptions as subscriptions_repo
from app.db.session import get_session as get_db_session
from app.deps import Context, costs_money, required_context
from app.services import gpt as gpt_service
from app.services import agent_analytics
from app.storage import get_storage

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/profiles", tags=["profiles"])


class ProfileView(BaseModel):
    id: str
    name: str
    kind: str
    is_default: bool
    # Идентификаторы снимков — чтобы набор можно было править, а не только
    # смотреть: правка присылает список целиком, и собрать его из ссылок
    # разбором строк значило бы держать формат ссылки в двух местах.
    media_ids: list[str] = []
    photo_urls: list[str] = []
    # Что из набора реально уезжает в кадр — по порядку полезности.
    reference_urls: list[str] = []

    @classmethod
    def of(cls, row) -> "ProfileView":
        return cls(
            id=row.id, name=row.name, kind=row.kind, is_default=row.is_default,
            media_ids=list(row.media_ids or []),
            photo_urls=[f"/api/media/{mid}" for mid in (row.media_ids or [])],
            reference_urls=[f"/api/media/{mid}" for mid in (row.reference_ids or [])],
        )


class CreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    # Пятнадцать — с запасом под обучение: личной модели нужно десять-двадцать
    # снимков, и набор, собранный сегодня, не придётся пересобирать завтра.
    #
    # Столько же снимков в кадр НЕ уезжает: в генерацию идёт один, и это
    # отдельная настройка. Хранить много и отдавать много — разные решения с
    # разной ценой ошибки.
    media_ids: list[str] = Field(min_length=1, max_length=15)
    kind: str = Field(default="person", pattern="^(person|pet)$")


@router.get("", response_model=list[ProfileView])
async def list_profiles(
    ctx: Context = Depends(required_context),
    db: AsyncSession = Depends(get_db_session),
) -> list[ProfileView]:
    """Все профили человека, основной первым.

    Заодно собирает профиль «Вы», если его ещё нет: отдельного экрана «загрузите
    пять фотографий» быть не должно — человек уже присылал свои снимки, по ним и
    соберём.
    """
    user, _ = ctx
    await profiles_repo.ensure_silent_profile(db, user.id)
    return [ProfileView.of(row) for row in await profiles_repo.list_for_user(db, user.id)]


@router.post("", response_model=ProfileView)
async def create_profile(
    body: CreateRequest,
    ctx: Context = Depends(costs_money),
    db: AsyncSession = Depends(get_db_session),
) -> ProfileView:
    """Завести профиль: себя, партнёра, ребёнка, питомца."""
    user, _ = ctx
    # AI-профиль — только по подписке (Илья, 2026-09-08). Приложение ведёт
    # человека к пейволу после десятой фотографии; здесь то же правило, чтобы
    # оно держалось не одним экраном. 402, как у нехватки монет: клиент это
    # уже умеет читать.
    if await subscriptions_repo.active_for_user(db, user.id) is None:
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED,
                            detail="AI profile is part of the subscription")
    # Тот же набор второй раз — тот же профиль, а не ещё один. Защёлки в
    # приложении не было, и каждое нажатие заводило новый: на тестовом
    # телефоне их стало восемь, три — за шесть секунд (21 сентября 2026).
    # Приложение теперь не шлёт второй запрос, но правило должно держаться и
    # здесь: повтор на плохой связи выглядит ровно так же.
    #
    # Имя при этом берётся новое. Одинаковый снимок хранится один раз, так что
    # те же десять селфи, выбранные заново, — это тот же набор; вернуть
    # профиль со старым именем значило молча выбросить только что набранное
    # (так и вышло: имя «не сохранялось», 21 сентября 2026).
    for existing in await profiles_repo.list_for_user(db, user.id):
        if set(existing.media_ids or []) == set(body.media_ids):
            name = body.name.strip()[:60]
            if name and name != existing.name:
                existing.name = name
                await db.flush()
            return ProfileView.of(existing)

    for media_id in body.media_ids:
        asset = await db.get(MediaAsset, media_id)
        if asset is None or asset.user_id != user.id or asset.deleted_at is not None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Photo not found")

    # Отбор опорных снимков — тем же разбором, что человек видел на экране.
    # Обычно он уже готов: приложение просит разбор, пока снимки грузятся, а
    # человек вводит имя. Раньше здесь модель смотрела на те же десять
    # снимков второй раз — секунды ожидания после «Continue» ради ответа,
    # который у нас уже был.
    verdict = await _verdict(db, user.id, body.media_ids)
    chosen = [body.media_ids[i - 1] for i in verdict["chosen"] if 1 <= i <= len(body.media_ids)]

    row = await profiles_repo.create(
        db, user_id=user.id, name=body.name, media_ids=body.media_ids, kind=body.kind,
        reference_ids=chosen,
    )
    return ProfileView.of(row)


class UpdateRequest(BaseModel):
    """Что можно поменять в готовом профиле.

    Оба поля необязательны: имя правят чаще, набор — реже, и заставлять
    присылать одно ради другого значит терять то, чего не прислали.
    """

    name: Optional[str] = Field(default=None, min_length=1, max_length=60)
    media_ids: Optional[list[str]] = Field(default=None, min_length=1, max_length=15)


@router.patch("/{profile_id}", response_model=ProfileView)
async def update_profile(
    profile_id: str,
    body: UpdateRequest,
    ctx: Context = Depends(costs_money),
    db: AsyncSession = Depends(get_db_session),
) -> ProfileView:
    """Переименовать профиль или поменять его набор снимков.

    Имя — не украшение списка. Профилей несколько, в кадр уходит выбранный, и
    отличить «Me» от «Me» человек не может никак; в совместном кадре это имя
    вдобавок уезжает в промпт и говорит модели, кто из двоих кто.

    Набор меняется целиком, а не по одному кадру: отбор опорных снимков смотрит
    на весь набор сразу — какие ракурсы уже есть, каких не хватает, — и
    пересчитывать его от добавления одной фотографии всё равно пришлось бы
    целиком.
    """
    user, _ = ctx
    row = await profiles_repo.get(db, profile_id, user_id=user.id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Unknown profile")

    if body.name is not None:
        row.name = body.name.strip()[:60] or row.name

    if body.media_ids is not None:
        for media_id in body.media_ids:
            asset = await db.get(MediaAsset, media_id)
            if asset is None or asset.user_id != user.id or asset.deleted_at is not None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Photo not found")
        row.media_ids = body.media_ids
        verdict = await _verdict(db, user.id, body.media_ids)
        row.reference_ids = [body.media_ids[i - 1] for i in verdict["chosen"]
                             if 1 <= i <= len(body.media_ids)]

    await db.flush()
    return ProfileView.of(row)


async def _images(db: AsyncSession, media_ids: list[str]) -> list[bytes]:
    """Снимки набора — разом, а не по одному.

    Хранилище в другой стране: десять снимков по очереди читаются за 2 с, все
    сразу — за 0,3 с (замер 21 сентября 2026). Порядок сохраняется: разбор
    отвечает номерами снимков, и номера обязаны совпасть с набором.
    """
    storage = get_storage()
    assets = [await db.get(MediaAsset, media_id) for media_id in media_ids]
    blobs = await asyncio.gather(*(storage.get(a.storage_key) if a else asyncio.sleep(0)
                                   for a in assets))
    return [b for b in blobs if b]


# Разбор набора помним два часа — между «загрузил» и «заплатил» проходит
# минута, а не день. Ключ — упорядоченный набор: разбор отвечает номерами.
_VERDICT_TTL = 2 * 60 * 60


def _verdict_key(user_id: str, media_ids: list[str]) -> str:
    digest = hashlib.sha256("|".join(media_ids).encode()).hexdigest()[:32]
    return f"profile_review:{user_id}:{digest}"


async def _recalled(user_id: str, media_ids: list[str]) -> Optional[dict]:
    try:
        from app.redis_client import get_client
        raw = await get_client().get(_verdict_key(user_id, media_ids))
        return json.loads(raw) if raw else None
    except Exception:  # память — ускорение, а не условие работы
        log.warning("Разбор набора: не прочитать из памяти", exc_info=True)
        return None


async def _remember(user_id: str, media_ids: list[str], verdict: dict) -> None:
    try:
        from app.redis_client import get_client
        await get_client().set(_verdict_key(user_id, media_ids), json.dumps(verdict),
                               ex=_VERDICT_TTL)
    except Exception:
        log.warning("Разбор набора: не записать в память", exc_info=True)


async def _verdict(db: AsyncSession, user_id: str, media_ids: list[str]) -> dict:
    """Разбор набора: из памяти, а нет — одним взглядом модели.

    Не разобрали — вердикт пустой: тогда в кадр пойдёт начало набора, и это
    лучше, чем случайный отбор, выданный за осмысленный. Пустой вердикт не
    запоминаем — следующая попытка вправе посмотреть снова.
    """
    remembered = await _recalled(user_id, media_ids)
    if remembered is not None:
        return remembered
    verdict = await gpt_service.review_profile_photos(await _images(db, media_ids))
    if verdict.get("photos") or verdict.get("chosen"):
        await _remember(user_id, media_ids, verdict)
    return verdict


@router.post("/{profile_id}/default", response_model=ProfileView)
async def set_default(
    profile_id: str,
    ctx: Context = Depends(required_context),
    db: AsyncSession = Depends(get_db_session),
) -> ProfileView:
    user, _ = ctx
    row = await profiles_repo.get(db, profile_id, user_id=user.id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found")
    await profiles_repo.make_default(db, row)
    return ProfileView.of(row)


@router.delete("/{profile_id}")
async def delete_profile(
    profile_id: str,
    ctx: Context = Depends(required_context),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Убрать профиль. Снимки остаются: они живут в библиотеке сами по себе."""
    user, _ = ctx
    row = await profiles_repo.get(db, profile_id, user_id=user.id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found")
    await profiles_repo.soft_delete(db, row)
    return {"ok": True}


class ReviewRequest(BaseModel):
    # Ровно столько же, сколько влезает в профиль: разбор должен уметь
    # посмотреть весь набор, а не первые двенадцать из пятнадцати.
    #
    # Каждый снимок — картинка в запросе к зрению, то есть деньги и секунды.
    # Пятнадцать штук по 512 пикселей стоят меньше цента, и это приемлемо; сотня
    # уже нет, поэтому предел здесь есть и он жёсткий.
    media_ids: list[str] = Field(min_length=1, max_length=15)


class PhotoVerdict(BaseModel):
    index: int
    ok: bool
    # Чем плох — словами и человеку: «в кадре двое», «лицо слишком мелкое».
    reason: str = ""


class ReviewResponse(BaseModel):
    photos: list[PhotoVerdict] = []
    # Номера отобранных снимков, лучший первым.
    chosen: list[int] = []
    # Какого снимка не хватает набору. Пусто — набор годится как есть.
    missing: list[str] = []


@router.post("/review", response_model=ReviewResponse)
@agent_analytics.in_session(agent_analytics.STUDIO)
async def review(
    body: ReviewRequest,
    ctx: Context = Depends(costs_money),
    db: AsyncSession = Depends(get_db_session),
) -> ReviewResponse:
    """Что из набора годится и какого снимка не хватает.

    Пустой ответ законен: зрение недоступно. Профиль тогда собирается как есть —
    отказывать человеку из-за того, что мы не смогли посмотреть, значило бы
    наказывать его за нашу неисправность.
    """
    user, _ = ctx
    for media_id in body.media_ids:
        asset = await db.get(MediaAsset, media_id)
        if asset is None or asset.user_id != user.id or asset.deleted_at is not None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Photo not found")

    # Тот же разбор возьмёт заведение профиля — второй раз модель не смотрит.
    verdict = await _verdict(db, user.id, body.media_ids)
    return ReviewResponse(
        photos=[PhotoVerdict(**p) for p in verdict["photos"]],
        missing=verdict["missing"],
        chosen=verdict["chosen"],
    )
