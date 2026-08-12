"""Media delivery.

Files live in a private bucket, so this is the only door to them: it checks who
is asking and then streams the bytes. It replaces the ``/uploads`` static mount,
which served every user's reference photos — faces included — to anyone who had
or guessed a link.

Why stream instead of redirecting to a signed URL: the app pins our certificate,
and a redirect would send it to a different host where the pin cannot match.
When a CDN arrives this becomes a redirect, and the pinning policy has to be
extended to the media host at the same time — one change, both places.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import models as m
from app.db.session import get_session as get_db_session
from app.deps import Context, optional_context
from app.storage import get_storage

router = APIRouter(prefix="/api/media", tags=["media"])


@router.get("/{media_id}")
async def get_media(
    media_id: str,
    thumb: bool = Query(default=False),
    ctx: Optional[Context] = Depends(optional_context),
    db: AsyncSession = Depends(get_db_session),
) -> Response:
    """Return an object the caller is entitled to.

    Entitlement is one of two things: it is theirs, or it belongs to a
    generation they explicitly shared.
    """
    asset = await db.get(m.MediaAsset, media_id)
    if asset is None or asset.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found")

    user = ctx[0] if ctx else None
    if user is None or asset.user_id != user.id:
        if not await _is_shared(db, asset):
            # 404 rather than 403: whether a file exists is itself information.
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found")

    key = asset.thumb_key if thumb and asset.thumb_key else asset.storage_key
    data = await get_storage().get(key)
    if data is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found")

    return Response(
        content=data,
        media_type="image/jpeg" if thumb else (asset.mime or "application/octet-stream"),
        headers={
            # Private: a shared cache must not keep someone's photo. The client
            # may keep it, since the id never points at different bytes.
            "Cache-Control": "private, max-age=86400",
        },
    )


async def _is_shared(db: AsyncSession, asset: m.MediaAsset) -> bool:
    from sqlalchemy import select

    stmt = select(m.Generation).where(
        m.Generation.result_media_id == asset.id,
        m.Generation.share_id.is_not(None),
        m.Generation.deleted_at.is_(None),
    )
    return await db.scalar(stmt) is not None
