"""Профили: чтение, создание и молчаливая сборка из того, что уже есть.

Отдельного экрана «загрузите пять фотографий» быть не должно. Человек уже
присылал свои снимки — по ним и собирается профиль «Вы», без единого вопроса.
Спрашивать заново то, что мы про него знаем, — это ровно та невнимательность,
из-за которой затевался весь разбор фраз.
"""
from __future__ import annotations

from typing import Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import models as m

# Сколько снимков держим в профиле, собирая его сами.
#
# Три, а не десять: набор нужен разный по ракурсу и свету, а подряд идущие
# загрузки почти всегда сняты в одном месте за одну минуту. Больше кадров из
# одного дня не делают профиль лучше — они делают его увереннее в том, что
# человек всегда в этой футболке.
SILENT_PROFILE_PHOTOS = 3


async def list_for_user(session: AsyncSession, user_id: str) -> Sequence[m.PersonProfile]:
    stmt = (
        select(m.PersonProfile)
        .where(m.PersonProfile.user_id == user_id, m.PersonProfile.deleted_at.is_(None))
        .order_by(m.PersonProfile.is_default.desc(), m.PersonProfile.created_at)
    )
    return (await session.scalars(stmt)).all()


async def get(session: AsyncSession, profile_id: str, *, user_id: str) -> Optional[m.PersonProfile]:
    profile = await session.get(m.PersonProfile, profile_id)
    if profile is None or profile.user_id != user_id or profile.deleted_at is not None:
        return None
    return profile


async def get_default(session: AsyncSession, user_id: str) -> Optional[m.PersonProfile]:
    stmt = (
        select(m.PersonProfile)
        .where(
            m.PersonProfile.user_id == user_id,
            m.PersonProfile.deleted_at.is_(None),
            m.PersonProfile.is_default.is_(True),
        )
        .limit(1)
    )
    return await session.scalar(stmt)


async def create(
    session: AsyncSession,
    *,
    user_id: str,
    name: str,
    media_ids: list[str],
    kind: str = "person",
    is_default: bool = False,
    reference_ids: list[str] | None = None,
) -> m.PersonProfile:
    """Завести профиль. Первый становится основным сам собой."""
    if is_default:
        await _clear_default(session, user_id)
    else:
        is_default = await get_default(session, user_id) is None

    profile = m.PersonProfile(
        user_id=user_id, name=name.strip()[:60] or "Me",
        kind=kind, media_ids=media_ids, reference_ids=reference_ids or [],
        is_default=is_default,
    )
    session.add(profile)
    await session.flush()
    return profile


def references(profile: m.PersonProfile, *, limit: int) -> list[str]:
    """Снимки, которые уедут в кадр.

    Отобранные, если разбор их назвал; иначе начало общего списка — молчаливый
    профиль собирается без зрения, и отбирать там некому.
    """
    chosen = list(profile.reference_ids or []) or list(profile.media_ids or [])
    return chosen[:max(1, limit)]


async def make_default(session: AsyncSession, profile: m.PersonProfile) -> None:
    await _clear_default(session, profile.user_id)
    profile.is_default = True
    await session.flush()


async def soft_delete(session: AsyncSession, profile: m.PersonProfile) -> None:
    """Убрать профиль.

    Снимки при этом остаются: они загружены человеком и живут в его библиотеке
    сами по себе. Удаление профиля — это «перестань подставлять этих людей», а
    не «сотри мои фотографии».
    """
    from datetime import datetime, timezone

    profile.deleted_at = datetime.now(timezone.utc)
    profile.is_default = False
    await session.flush()


async def _clear_default(session: AsyncSession, user_id: str) -> None:
    current = await get_default(session, user_id)
    if current is not None:
        current.is_default = False
        await session.flush()


async def ensure_silent_profile(session: AsyncSession, user_id: str) -> Optional[m.PersonProfile]:
    """Собрать профиль «Вы» из снимков, которые человек уже использовал как себя.

    Использованным считается тот, что уехал исходником генерации: это факт, а не
    догадка — на остальных загрузках может быть что угодно, от постера-образца до
    фотографии кота.

    Возвращает `None`, если собирать не из чего. Это законно и часто: человек мог
    ещё ни разу не приложить своё лицо.
    """
    existing = await get_default(session, user_id)
    if existing is not None:
        return existing

    used = (
        select(m.MediaAsset.id)
        .join(m.Generation, m.Generation.source_media_id == m.MediaAsset.id)
        .where(
            m.MediaAsset.user_id == user_id,
            m.MediaAsset.kind == "upload",
            m.MediaAsset.deleted_at.is_(None),
        )
        .group_by(m.MediaAsset.id)
        .order_by(m.MediaAsset.id)
        .limit(SILENT_PROFILE_PHOTOS)
    )
    media_ids = list((await session.scalars(used)).all())
    if not media_ids:
        return None

    return await create(session, user_id=user_id, name="Me", media_ids=media_ids,
                        is_default=True)
