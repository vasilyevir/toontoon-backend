"""Media repository: the only place that knows how a file becomes a row.

Uploads, results and masks all pass through here, so metadata stripping,
thumbnails, deduplication and key naming happen once instead of at every call
site — which is how ``uploads/`` ended up scattered across two services.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import models as m
from app.storage import get_storage, make_key
from app.storage import images

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
