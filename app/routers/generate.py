"""Generation endpoints: file upload + the two-phase generate flow."""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from app.config import settings
from app.core import rate_limit
from app.core.security import new_id
from app.deps import Context, required_context
from app.models.generation import (
    Generation,
    GenerateRequest,
    GenerateResponse,
    GenerationStatus,
    GenerationType,
)
from app.services import content_gen, generations_service, tiles_data, video_gen, wallet

logger = logging.getLogger("arteki.generate")

router = APIRouter(prefix="/api", tags=["generate"])

UPLOAD_DIR = Path("uploads")
_ALLOWED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}


@router.post("/uploads")
async def upload(file: UploadFile = File(...), ctx: Context = Depends(required_context)):
    """Store a reference photo and return a URL usable as ``photo_url``."""
    if file.content_type not in _ALLOWED_IMAGE_TYPES:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="Unsupported image type")

    UPLOAD_DIR.mkdir(exist_ok=True)
    suffix = Path(file.filename or "").suffix or ".png"
    name = f"{new_id()}{suffix}"
    path = UPLOAD_DIR / name
    path.write_bytes(await file.read())

    return {"id": name, "url": f"{settings.public_base_url}/uploads/{name}"}


@router.post("/generate", response_model=GenerateResponse)
async def generate(body: GenerateRequest, ctx: Context = Depends(required_context)) -> GenerateResponse:
    """Two-phase generation:

    1. reserve TEKI (create payment)
    2. run the generation
    3. confirm on success / cancel (refund) on failure
    """
    user, session = ctx

    # Rate limit: N generations per hour per user.
    allowed, _ = await rate_limit.hit(f"gen:{user.id}", settings.rate_limit_per_hour, 3600)
    if not allowed:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, detail="Generation rate limit reached")

    tile = tiles_data.get_tile(body.tile_id) if body.tile_id else None
    if body.tile_id and tile is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Unknown tile")

    # Cost is driven by the tile (videos cost more) or the requested type.
    if tile is not None:
        tile_is_video = tile.category.value == "video"
        # Reject a request whose declared type contradicts the tile's category
        # BEFORE reserving TEKI, so a mismatch never charges the wrong amount
        # or routes into the wrong pipeline (logic-fix 7.3 / QA 9.4).
        if body.type == GenerationType.VIDEO and not tile_is_video:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail="Requested type does not match the selected template.",
            )
        if body.type == GenerationType.IMAGE and tile_is_video:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail="Requested type does not match the selected template.",
            )
        cost = tile.cost
        gen_type = GenerationType.VIDEO if tile_is_video else GenerationType.IMAGE
    else:
        gen_type = body.type
        cost = settings.video_teki_cost if gen_type == GenerationType.VIDEO else settings.image_teki_cost

    reason = f"arteki:{gen_type.value}_generate"
    try:
        payment = await wallet.reserve(user, session, amount=cost, reason=reason)
    except wallet.InsufficientFunds:
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, detail="Not enough TEKI")

    # ── Video: long-running (keyframes → Seedance, ~4–5 min). Run as a background
    # job and let the client poll GET /api/generations/{id}. We persist a QUEUED
    # record now; the worker flips it to DONE (with the URL) or FAILED (+ refund).
    if gen_type == GenerationType.VIDEO:
        if not settings.kie_enabled:
            await wallet.cancel(user, session, payment)
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Video generator is not configured. Your TEKI was refunded.",
            )

        generation = Generation(
            id=new_id("gen_"),
            user_id=user.id,
            type=GenerationType.VIDEO,
            status=GenerationStatus.QUEUED,
            tile_id=tile.id if tile else None,
            tile_label=tile.title if tile else None,
            prompt=body.prompt or (tile.title if tile else ""),
            payment_id=payment.payment_id,
            cost=cost,
        )
        await generations_service.add_for_user(generation)

        video_gen.schedule_video_job(
            gen_id=generation.id,
            user_id=user.id,
            session_id=session.sid,
            payment_amount=cost,
            payment_id=payment.payment_id,
            tile=tile,
            answers=body.answers,
            free_text=body.prompt,
            style=body.style,
            photo_url=body.photo_url,
        )

        balance = await wallet.get_balance(user, session)
        return GenerateResponse(
            id=generation.id,
            url="",
            type=GenerationType.VIDEO,
            balance=balance.available,
            prompt=generation.prompt,
            status=GenerationStatus.QUEUED,
        )

    try:
        result_url, prompt = await content_gen.generate(
            gen_type=gen_type,
            tile=tile,
            answers=body.answers,
            free_text=body.prompt,
            style=body.style,
            photo_url=body.photo_url,
        )
    except content_gen.GenerationUnavailable:
        # Upstream generator unavailable — refund and tell the client to retry.
        await wallet.cancel(user, session, payment)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Image generator is busy right now, your TEKI was refunded — please try again.",
        )
    except Exception:
        logger.exception("Generation failed for user %s (tile=%s)", user.id, body.tile_id)
        await wallet.cancel(user, session, payment)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail="Generation failed")

    await wallet.confirm(user, session, payment)

    generation = Generation(
        id=new_id("gen_"),
        user_id=user.id,
        type=gen_type,
        status=GenerationStatus.DONE,
        tile_id=tile.id if tile else None,
        tile_label=tile.title if tile else None,
        prompt=prompt,
        result_url=result_url,
        payment_id=payment.payment_id,
        cost=cost,
    )
    await generations_service.add_for_user(generation)

    balance = await wallet.get_balance(user, session)
    return GenerateResponse(
        id=generation.id,
        url=result_url,
        type=gen_type,
        balance=balance.available,
        prompt=prompt,
    )
