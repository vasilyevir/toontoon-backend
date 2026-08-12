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
        "prompt": row.prompt,
        "result_url": result,
        "thumbnail_url": thumb,
        "cost": row.cost,
        "style_id": row.style_id,
        "share_id": row.share_id,
        "created_at": row.created_at.isoformat(),
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


@router.get("/share/{share_id}", tags=["public"])
async def get_shared(share_id: str, db: AsyncSession = Depends(get_db_session)) -> dict:
    """Public view of a deliberately shared work. Everything else stays private."""
    row = await generations_repo.get_by_share_id(db, share_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found")
    return _serialize(row)
