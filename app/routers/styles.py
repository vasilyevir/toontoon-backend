"""Catalogue endpoints: the home showcase, a style card, and today's Shots.

Replaces ``/api/tiles``. The top level is the six directions from onboarding
(CH-12/CH-14) — the old Image / Postcard / Video described what came out, these
describe what the person came for.

The catalogue itself is empty until styles are written against the chosen
generation model (CH-19): an endpoint that honestly returns nothing is better
than one padded with placeholders nobody can generate.
"""
from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import models as m
from app.db.repositories import styles as styles_repo
from app.db.session import get_session as get_db_session
from app.deps import Context, optional_context
from app.routers.onboarding import CATEGORIES
from app.storage import get_storage

router = APIRouter(prefix="/api", tags=["catalog"])

# Titles are English: the interface ships in English (CH-12).
CATEGORY_TITLES = {
    "ai_photo_studio": "AI Photo Studio",
    "black_and_white": "Black & White",
    "glow_up": "Editorial Glow-Up",
    "paparazzi_flash": "Paparazzi Flash",
    "polaroid_reunion": "Polaroid Reunion",
    "artistic_touch": "Artistic Touch",
    "cartoon_me": "Cartoon Me",
    "lifestyle_travel": "Lifestyle & Travel",
    "fantasy_mode": "Fantasy Mode",
    "pet_magic": "Pet Magic",
    "family_fun": "Family Fun",
}


class StyleOut(BaseModel):
    id: str
    title: str
    description: Optional[str] = None
    category: str
    operation: str
    # What the launch screen must ask for. The client builds the screen from
    # this, which is what lets a new operation ship without a new screen (CH-21).
    input_spec: dict
    #: Кого рисуем: «person» или «pet». Пусто — человека.
    #:
    #: Наружу вынесено затем, что от этого зависит не только промпт, но и экран:
    #: у стиля про животное профиль человека бесполезен, и предлагать «Generate
    #: with profile» значит обещать кадр с чужим лицом вместо своей собаки.
    subject: Optional[str] = None
    cost: int
    examples: list[str] = []


class CategoryOut(BaseModel):
    id: str
    title: str
    styles: list[StyleOut]


def _example_keys(row: m.Style) -> list[str]:
    return (row.examples or {}).get("keys", []) if isinstance(row.examples, dict) else []


def _style_out(row: m.Style) -> StyleOut:
    return StyleOut(
        id=row.id,
        title=row.title,
        description=row.description,
        category=row.category,
        operation=row.operation,
        input_spec=row.input_spec or {},
        subject=(row.prompt_template or {}).get("subject"),
        cost=row.cost,
        # Примеры каталога отдаёт свой публичный маршрут, а не /api/media:
        # там проверка владельца, а у витринной картинки владельца нет и быть
        # не должно. Класть её в чужие медиа значит либо ослабить проверку,
        # либо завести фиктивного пользователя ради маркетинга.
        # `?v=` — отпечаток картинки из имени ключа. Маршрут индексный, адрес
        # без версии не менялся бы при замене примера, и клиент показывал бы
        # кэш до истечения суток.
        examples=[
            f"/api/styles/{row.id}/example/{index}?v={Path(key).stem.split('-')[-1]}"
            for index, key in enumerate(_example_keys(row))
        ],
    )


async def _liked_categories(db: AsyncSession, ctx: Optional[Context]) -> list[str]:
    if ctx is None:
        return []
    prefs = await db.get(m.UserPreferences, ctx[0].id)
    return list(prefs.liked_categories or []) if prefs else []


@router.get("/styles", response_model=list[CategoryOut])
async def list_styles(
    ctx: Optional[Context] = Depends(optional_context),
    db: AsyncSession = Depends(get_db_session),
) -> list[CategoryOut]:
    """The whole catalogue, grouped by direction.

    Порядок разделов — тот, что выставлен вручную в `CATEGORIES`, и он один
    для всех. Внутри раздела — тоже заданный.

    Персонализация (♥ из онбординга наверх) написана и лежит рядом, но
    выключена флагом `personalise_catalogue`. Она переворачивала верх витрины:
    человек, отметивший шесть направлений из одиннадцати, видел первыми их, и
    выстроенный порядок начинал работать только с седьмой ленты. Пока задача —
    первое впечатление, а оно должно быть одинаковым и предсказуемым.
    """
    # Скрытые разделы не попадают в витрину вовсе: ни лентой, ни заголовком.
    ordering = [c for c in CATEGORIES if c not in settings.hidden_category_list]
    if settings.personalise_catalogue:
        liked = await _liked_categories(db, ctx)
        ordering = ([c for c in ordering if c in liked] + [c for c in ordering if c not in liked])

    result: list[CategoryOut] = []
    for category in ordering:
        rows = await styles_repo.list_styles(db, category=category)
        result.append(
            CategoryOut(
                id=category,
                title=CATEGORY_TITLES.get(category, category),
                styles=[_style_out(r) for r in rows],
            )
        )
    return result


@router.get("/styles/home", response_model=list[StyleOut])
async def home_styles(
    ctx: Optional[Context] = Depends(optional_context),
    db: AsyncSession = Depends(get_db_session),
    limit: int = Query(default=8, ge=1, le=24),
) -> list[StyleOut]:
    """The showcase: a few strong examples, not a grid of everything (CH-14).

    Ранжирование под человека — тем же флагом, что и порядок разделов: иначе
    ленты шли бы одинаково у всех, а витрина над ними — по-разному, и первое
    впечатление опять зависело бы от ответов в онбординге.
    """
    liked = await _liked_categories(db, ctx) if settings.personalise_catalogue else []
    rows = await styles_repo.list_styles(db, home_only=True, limit=limit * 3)
    ranked = styles_repo.rank_for(rows, liked)[:limit]
    return [_style_out(r) for r in ranked]


@router.get("/styles/{style_id}/example/{index}")
async def style_example(
    style_id: str, index: int, db: AsyncSession = Depends(get_db_session)
) -> Response:
    """Витринная картинка стиля — публично и с длинным кэшем.

    Это единственная картинка в системе, которую можно отдавать кому угодно:
    она наша, снята для каталога и показывается до входа. Всё остальное живёт
    за `/api/media` с проверкой владельца.
    """
    row = await styles_repo.get(db, style_id)
    keys = _example_keys(row) if row else []
    if not keys or index < 0 or index >= len(keys):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found")

    data = await _example_bytes(keys[index])
    if data is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found")

    return Response(
        content=data,
        media_type="image/jpeg",
        # Содержимое по этому адресу не меняется: замена примера — это новый
        # ключ в строке стиля, а не другие байты по старому адресу.
        headers={"Cache-Control": "public, max-age=86400, immutable"},
    )


# ── Витринные картинки в памяти ──────────────────────────────────────────────
#
# Одинаковые для всех, неизменные (замена — это новый ключ) и нужные первыми:
# главная при первом запуске тянет их десятками. Держать их в памяти процесса
# дешевле, чем каждый раз ходить в хранилище в другой стране — это было
# 0,5–2,5 с на картинку, и верхние карточки не успевали появиться до экрана
# награды на седьмой секунде (задача #7 Андрея, 21 сентября 2026).
#
# Потолок — чтобы разросшийся каталог не съел память: вытесняется то, что
# давно не спрашивали. Сотня стилей с примерами — десятки мегабайт.

log = logging.getLogger("toontoon.styles")

_EXAMPLES_CAP = 128 * 1024 * 1024
_examples: "OrderedDict[str, bytes]" = OrderedDict()
_examples_size = 0


def _remember_example(key: str, data: bytes) -> None:
    global _examples_size
    if key in _examples:
        return
    _examples[key] = data
    _examples_size += len(data)
    while _examples_size > _EXAMPLES_CAP and _examples:
        _, old = _examples.popitem(last=False)
        _examples_size -= len(old)


async def _example_bytes(key: str) -> Optional[bytes]:
    data = _examples.get(key)
    if data is not None:
        _examples.move_to_end(key)
        return data
    data = await get_storage().get(key)
    if data is not None:
        _remember_example(key, data)
    return data


async def warm_examples(concurrency: int = 8) -> int:
    """Прогреть витрину при старте — в фоне, не задерживая запуск.

    Сначала главная (в её порядке), потом остальной каталог: первым делом
    человек видит главную, и именно её картинки должны быть готовы. Ошибка
    одной картинки прогрев не останавливает — её дочитают по первому запросу.
    """
    from app.db.session import get_factory

    async with get_factory()() as db:
        home = await styles_repo.list_styles(db, home_only=True, limit=500)
        rest = await styles_repo.list_styles(db, limit=1000)
    keys: list[str] = []
    for row in [*home, *rest]:
        for key in _example_keys(row):
            if key not in keys:
                keys.append(key)

    gate = asyncio.Semaphore(concurrency)

    async def one(key: str) -> bool:
        async with gate:
            try:
                return await _example_bytes(key) is not None
            except Exception:  # noqa: BLE001 — прогрев не обязателен
                return False

    done = sum(await asyncio.gather(*(one(k) for k in keys)))
    log.info("Витрина прогрета: %d из %d картинок, %.1f МБ в памяти",
             done, len(keys), _examples_size / 1e6)
    return done


@router.get("/styles/{style_id}", response_model=StyleOut)
async def get_style(style_id: str, db: AsyncSession = Depends(get_db_session)) -> StyleOut:
    row = await styles_repo.get(db, style_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Unknown style")
    return _style_out(row)


@router.get("/shots/daily", response_model=list[StyleOut])
async def daily_shots(
    db: AsyncSession = Depends(get_db_session),
    day: Optional[str] = Query(default=None, description="YYYY-MM-DD (UTC), по умолчанию сегодня"),
    count: Optional[int] = Query(default=None, ge=1, le=60,
                                 description="Сколько карточек; по умолчанию — настройка сервера"),
) -> list[StyleOut]:
    """Today's set. Same for everyone, reproducible for any past date."""
    try:
        target = (
            datetime.strptime(day, "%Y-%m-%d").date()
            if day
            else datetime.now(timezone.utc).date()
        )
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="day must be YYYY-MM-DD") from None
    rows = await styles_repo.daily_shots(db, target, count=count)
    return [_style_out(r) for r in rows]
