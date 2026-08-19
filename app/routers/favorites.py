"""Сохранённые стили.

Ручек три, и все они про множество, а не про запись: отдать список, добавить,
убрать. Добавление идёт списком, потому что главный его случай — не одна
закладка, а первый вход: приложение до этого хранило закладки у себя и присылает
их разом.

Слияние только добавляет. Человек, ставивший закладки на двух устройствах,
ожидает их объединение; замена набора означала бы, что последний вход стёр
предыдущий, и восстановить это уже нечем.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import models as m
from app.db.session import get_session as get_db_session
from app.deps import Context, required_context

router = APIRouter(prefix="/api/favorites", tags=["favorites"])

# Потолок на присылаемый список: за ним не стоит ничего, кроме нежелания
# принимать чужую фантазию целиком. Каталог на порядок меньше.
MAX_IDS = 500


class FavoriteIDs(BaseModel):
    style_ids: list[str] = Field(default_factory=list, max_length=MAX_IDS)


@router.get("")
async def list_favorites(
    ctx: Context = Depends(required_context),
    db: AsyncSession = Depends(get_db_session),
) -> FavoriteIDs:
    """Закладки в порядке добавления — старые первыми."""
    user, _ = ctx
    rows = await db.scalars(
        select(m.StyleFavorite.style_id)
        .where(m.StyleFavorite.user_id == user.id)
        .order_by(m.StyleFavorite.created_at)
    )
    return FavoriteIDs(style_ids=list(rows))


@router.post("")
async def add_favorites(
    body: FavoriteIDs,
    ctx: Context = Depends(required_context),
    db: AsyncSession = Depends(get_db_session),
) -> FavoriteIDs:
    """Добавить закладки и вернуть весь набор.

    Повторное добавление того же стиля — не ошибка, а обычное дело: то же
    приложение может прислать свой список дважды после неудачной сети.
    Поэтому конфликт по ключу игнорируется, а не поднимает исключение.
    """
    user, _ = ctx
    unique = list(dict.fromkeys(body.style_ids))
    if unique:
        await db.execute(
            insert(m.StyleFavorite)
            .values([{"user_id": user.id, "style_id": sid} for sid in unique])
            .on_conflict_do_nothing(index_elements=["user_id", "style_id"])
        )
        await db.flush()
    return await list_favorites(ctx=ctx, db=db)


@router.delete("/{style_id}")
async def remove_favorite(
    style_id: str,
    ctx: Context = Depends(required_context),
    db: AsyncSession = Depends(get_db_session),
) -> FavoriteIDs:
    """Убрать закладку. Отсутствие такой закладки — тоже успех: результат тот,
    которого просили."""
    user, _ = ctx
    await db.execute(
        delete(m.StyleFavorite).where(
            m.StyleFavorite.user_id == user.id,
            m.StyleFavorite.style_id == style_id,
        )
    )
    await db.flush()
    return await list_favorites(ctx=ctx, db=db)
