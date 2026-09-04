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

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MediaAsset
from app.db.repositories import profiles as profiles_repo
from app.db.session import get_session as get_db_session
from app.deps import Context, costs_money, required_context
from app.services import gpt as gpt_service
from app.services import agent_analytics
from app.storage import get_storage

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
    for media_id in body.media_ids:
        asset = await db.get(MediaAsset, media_id)
        if asset is None or asset.user_id != user.id or asset.deleted_at is not None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Photo not found")

    # Отбор опорных снимков — здесь же, одним взглядом на весь набор. Отдельным
    # вызовом это стоило бы вдвое дороже и могло разойтись с разбором: человек
    # увидел бы одни вердикты, а в кадр уехало бы другое.
    chosen = await _chosen_references(db, body.media_ids)

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
        row.reference_ids = await _chosen_references(db, body.media_ids)

    await db.flush()
    return ProfileView.of(row)


async def _chosen_references(db: AsyncSession, media_ids: list[str]) -> list[str]:
    """Какие снимки набора поедут в кадр.

    Смотрим на весь набор одним взглядом — тем же, что и при сборке профиля.
    Не разобрали — оставляем список пустым: тогда в кадр пойдёт начало набора,
    и это лучше, чем случайный отбор, выданный за осмысленный.
    """
    storage = get_storage()
    images: list[bytes] = []
    for media_id in media_ids:
        asset = await db.get(MediaAsset, media_id)
        data = await storage.get(asset.storage_key) if asset else None
        if data:
            images.append(data)

    verdict = await gpt_service.review_profile_photos(images)
    return [media_ids[i - 1] for i in verdict["chosen"] if 1 <= i <= len(media_ids)]


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
    storage = get_storage()
    images: list[bytes] = []
    for media_id in body.media_ids:
        asset = await db.get(MediaAsset, media_id)
        if asset is None or asset.user_id != user.id or asset.deleted_at is not None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Photo not found")
        data = await storage.get(asset.storage_key)
        if data:
            images.append(data)

    verdict = await gpt_service.review_profile_photos(images)
    return ReviewResponse(
        photos=[PhotoVerdict(**p) for p in verdict["photos"]],
        missing=verdict["missing"],
        chosen=verdict["chosen"],
    )
