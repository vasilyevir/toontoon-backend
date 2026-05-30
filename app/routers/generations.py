"""Library, sharing and public share view."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status

from app.config import settings
from app.core.security import new_id
from app.deps import Context, optional_context, required_context
from app.models.generation import (
    CreateGenerationRequest,
    Generation,
    GenerationStatus,
    ShareResponse,
)
from app.services import generations_service, tiles_data

router = APIRouter(prefix="/api", tags=["generations"])


@router.get("/generations", response_model=list[Generation])
async def list_generations(ctx: Optional[Context] = Depends(optional_context)) -> list[Generation]:
    """The user's library. Returns ``[]`` when not authenticated."""
    if ctx is None:
        return []
    user, _ = ctx
    return await generations_service.list_for_user(user.id)


@router.post("/generations", response_model=Generation, status_code=status.HTTP_201_CREATED)
async def create_generation(body: CreateGenerationRequest, ctx: Context = Depends(required_context)) -> Generation:
    """Reserve a queued generation record up-front (before /generate runs)."""
    user, _ = ctx
    tile = tiles_data.get_tile(body.tile_id) if body.tile_id else None
    generation = Generation(
        id=new_id("gen_"),
        user_id=user.id,
        type=body.type,
        status=GenerationStatus.QUEUED,
        tile_id=tile.id if tile else None,
        tile_label=tile.title if tile else None,
        prompt=body.prompt,
    )
    await generations_service.add_for_user(generation)
    return generation


@router.post("/generations/{gen_id}/share", response_model=ShareResponse)
async def share_generation(gen_id: str, ctx: Context = Depends(required_context)) -> ShareResponse:
    user, _ = ctx
    generation = await generations_service.get(gen_id)
    if not generation or generation.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Generation not found")
    share_id = await generations_service.create_share(generation)
    return ShareResponse(share_id=share_id, share_url=f"{settings.frontend_url}/v/{share_id}")


@router.get("/share/{share_id}", response_model=Generation, tags=["public"])
async def public_share(share_id: str) -> Generation:
    """Public, unauthenticated view backing the ``/v/[shareId]`` page."""
    generation = await generations_service.get_by_share(share_id)
    if not generation:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Share not found")
    return generation
