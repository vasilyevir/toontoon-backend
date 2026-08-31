"""Media repository: the only place that knows how a file becomes a row.

Uploads, results and masks all pass through here, so metadata stripping,
thumbnails, deduplication and key naming happen once instead of at every call
site — which is how ``uploads/`` ended up scattered across two services.
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import models as m
from app.storage import get_storage, make_key
from app.storage import images

logger = logging.getLogger("toontoon.media")

_EXT_BY_MIME = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}


async def find_upload_by_hash(
    session: AsyncSession, user_id: str, content_hash: str
) -> Optional[m.MediaAsset]:
    stmt = select(m.MediaAsset).where(
        m.MediaAsset.user_id == user_id,
        m.MediaAsset.content_hash == content_hash,
        m.MediaAsset.kind == "upload",
        m.MediaAsset.deleted_at.is_(None),
    )
    return await session.scalar(stmt)


async def has_upload(session: AsyncSession, user_id: str) -> bool:
    """Присылал ли человек когда-нибудь свой снимок.

    Спрашивается в разговоре: тому, кто уже делал кадры со своим лицом, стоит
    напомнить приложить фотографию, а тому, кто ни разу, — не стоит. Разница
    между напоминанием и приставанием ровно в этом факте.
    """
    stmt = select(m.MediaAsset.id).where(
        m.MediaAsset.user_id == user_id,
        m.MediaAsset.kind == "upload",
        m.MediaAsset.deleted_at.is_(None),
    ).limit(1)
    return await session.scalar(stmt) is not None


async def last_person_photo(session: AsyncSession, user_id: str) -> Optional[m.MediaAsset]:
    """Снимок, который человек последним использовал как себя.

    Не «последняя загрузка»: загрузить он мог образец стиля или чужую картинку.
    Использованным как себя считается тот, что уехал исходником генерации, —
    это факт, а не догадка. Если таких нет, берём последнюю загрузку: человек
    её зачем-то прислал, и других кандидатов у нас всё равно нет.
    """
    used = (
        select(m.MediaAsset)
        .join(m.Generation, m.Generation.source_media_id == m.MediaAsset.id)
        .where(
            m.MediaAsset.user_id == user_id,
            m.MediaAsset.kind == "upload",
            m.MediaAsset.deleted_at.is_(None),
        )
        .order_by(m.Generation.created_at.desc())
        .limit(1)
    )
    asset = await session.scalar(used)
    if asset is not None:
        return asset

    latest = (
        select(m.MediaAsset)
        .where(
            m.MediaAsset.user_id == user_id,
            m.MediaAsset.kind == "upload",
            m.MediaAsset.deleted_at.is_(None),
        )
        .order_by(m.MediaAsset.created_at.desc())
        .limit(1)
    )
    return await session.scalar(latest)


async def save_image(
    session: AsyncSession,
    *,
    user_id: str,
    kind: str,
    data: bytes,
    make_thumbnail: bool = True,
) -> m.MediaAsset:
    """Process, store and register an image.

    Uploading the same photo twice returns the existing asset rather than a
    second copy — people re-pick the same picture constantly, and each copy would
    be paid for in storage forever.
    """
    processed = images.process(data, make_thumbnail=make_thumbnail)

    if kind == "upload":
        existing = await find_upload_by_hash(session, user_id, processed.sha256)
        if existing is not None:
            return existing

    storage = get_storage()
    ext = _EXT_BY_MIME.get(processed.mime, ".bin")
    key = make_key(user_id=user_id, kind=kind, ext=ext)

    # Object first, row second: an orphaned object is collectable garbage, while
    # a row pointing at nothing is a broken screen for the user.
    await storage.put(key, processed.data, content_type=processed.mime)

    thumb_key: Optional[str] = None
    if processed.thumbnail:
        thumb_key = f"{key}.thumb.jpg"
        await storage.put(thumb_key, processed.thumbnail, content_type="image/jpeg")

    asset = m.MediaAsset(
        user_id=user_id,
        kind=kind,
        storage_key=key,
        thumb_key=thumb_key,
        mime=processed.mime,
        bytes=len(processed.data),
        width=processed.width,
        height=processed.height,
        content_hash=processed.sha256,
    )
    session.add(asset)
    await session.flush()
    return asset


async def signed_url(asset: m.MediaAsset, *, thumb: bool = False) -> Optional[str]:
    """Short-lived link for a client. The bucket itself stays closed."""
    key = asset.thumb_key if thumb and asset.thumb_key else asset.storage_key
    if not key:
        return None
    return await get_storage().signed_url(key)


async def erase_everything_of(session: AsyncSession, user_id: str) -> int:
    """Стереть все файлы человека из хранилища. Возвращает, сколько стёрлось.

    Вызывается при удалении аккаунта. До этого удаление обезличивало строку —
    почта, имя и картинка в null, — а снимки оставались лежать. То есть человек
    нажимал «удалить», ему отвечали «готово», и его лицо продолжало храниться у
    нас. Хуже этого только не дать кнопку вовсе.

    Стираем байты и помечаем строки, а не удаляем их: на медиа ссылаются работы,
    а на работы — книга проводок, и база справедливо не даст порвать эту цепь.
    После стирания строка остаётся пустым следом: по ней видно, что файл был и
    что его больше нет.

    По одному, а не пачкой: хранилище может отказать на любом ключе — файла уже
    нет, сеть моргнула, — и падение на третьем снимке из двадцати оставило бы
    остальные семнадцать лежать. Отказ на одном не должен спасать другие.
    """
    storage = get_storage()
    rows = (await session.scalars(
        select(m.MediaAsset).where(
            m.MediaAsset.user_id == user_id,
            m.MediaAsset.deleted_at.is_(None),
        )
    )).all()

    стёрто = 0
    for asset in rows:
        for key in (asset.storage_key, asset.thumb_key):
            if not key:
                continue
            try:
                await storage.delete(key)
            except Exception:
                # Ключа может не быть — файл уже стирали, или хранилище моргнуло.
                # Строку всё равно помечаем: она обещает, что файла нет, и это
                # обещание надо выполнить хотя бы на нашей стороне.
                logger.warning("не стёрся файл %s пользователя %s", key, user_id)
        asset.deleted_at = func.now()
        стёрто += 1

    await session.flush()
    return стёрто


async def soft_delete(session: AsyncSession, asset: m.MediaAsset) -> None:
    """Hide the row and erase the bytes.

    Deleting is not just hiding: these are people's photographs, and "deleted"
    has to mean the file is gone from storage too.
    """
    storage = get_storage()
    await storage.delete(asset.storage_key)
    if asset.thumb_key:
        await storage.delete(asset.thumb_key)
    from sqlalchemy import func

    asset.deleted_at = func.now()
    await session.flush()
