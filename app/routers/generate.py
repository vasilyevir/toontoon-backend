"""Generation endpoints: file upload + the two-phase generate flow."""
from __future__ import annotations

import base64
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from app.config import settings
from app.core import rate_limit
from app.core.security import new_id
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session as get_db_session
from app.db.repositories import generations as generations_repo
from app.db.repositories import media as media_repo
from app.db.models import MediaAsset
from app.storage import get_storage
from app.deps import Context, required_context
from app.models.chat import ChatMessage, ChatRole
from app.models.generation import (
    Generation,
    GenerateRequest,
    GenerateResponse,
    GenerationStatus,
    GenerationType,
)
from app.services import chat_service, content_gen, generations_service, tiles_data, video_gen, wallet

logger = logging.getLogger("arteki.generate")

router = APIRouter(prefix="/api", tags=["generate"])

UPLOAD_DIR = Path("uploads")
_ALLOWED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}


@router.post("/uploads")
async def upload(
    file: UploadFile = File(...),
    ctx: Context = Depends(required_context),
    db: AsyncSession = Depends(get_db_session),
):
    """Store a reference photo and return a handle usable as ``photo_url``.

    The file goes into the private bucket, not into a served directory: these
    are photographs of people, and until now anyone with the link could open
    them. On the way in the metadata is stripped (phone photos carry the GPS
    coordinates of where they were taken) and the same picture uploaded twice
    is stored once.
    """
    if file.content_type not in _ALLOWED_IMAGE_TYPES:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="Unsupported image type")

    user, _ = ctx
    data = await file.read()
    try:
        asset = await media_repo.save_image(db, user_id=user.id, kind="upload", data=data)
    except Exception as exc:  # noqa: BLE001 — a broken image is a client error
        logger.warning("Upload rejected for user %s: %r", user.id, exc)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Could not read this image")

    return {"id": asset.id, "url": f"/api/media/{asset.id}"}


@router.post("/generate", response_model=GenerateResponse)
async def generate(
    body: GenerateRequest,
    ctx: Context = Depends(required_context),
    db: AsyncSession = Depends(get_db_session),
) -> GenerateResponse:
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

    # Optional: if this generation belongs to a chat session, the result (or
    # error, on failure) is appended there automatically. Validate ownership
    # up front so we fail fast, before reserving TEKI.
    chat = None
    if body.chat_id:
        chat = await chat_service.get(body.chat_id)
        if not chat or chat.user_id != user.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Chat not found")

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
    # Note on refunds in this handler: the request runs inside one database
    # transaction, and raising rolls it back — so a failed image generation
    # leaves no charge at all, rather than a charge plus a refund. The explicit
    # cancel() calls below are kept because they are the correct behaviour in
    # any path that does NOT roll back (the video worker, which commits the
    # charge before the job starts), and because they are idempotent.
    try:
        payment = await wallet.reserve(db, user.id, amount=cost, reason=reason)
    except wallet.InsufficientFunds:
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, detail="Not enough TEKI")

    # ── Video: long-running (keyframes → Seedance, ~4–5 min). Run as a background
    # job and let the client poll GET /api/generations/{id}. We persist a QUEUED
    # record now; the worker flips it to DONE (with the URL) or FAILED (+ refund).
    if gen_type == GenerationType.VIDEO:
        if not settings.kie_enabled:
            await wallet.cancel(db, user.id, payment)
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
            chat_id=chat.id if chat else None,
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
            chat_id=chat.id if chat else None,
        )

        balance = await wallet.get_balance(db, user.id)
        return GenerateResponse(
            id=generation.id,
            url="",
            type=GenerationType.VIDEO,
            balance=balance.available,
            prompt=generation.prompt,
            status=GenerationStatus.QUEUED,
        )

    # The reference photo now lives in private storage, so it is handed to the
    # generator as inline data instead of a URL it could fetch. Nothing about a
    # user's face should be reachable by a link.
    photo_data_url = await _resolve_photo(db, user.id, body.photo_url)

    # The attempt is recorded before the provider is called, so a failure leaves
    # a trace instead of nothing at all.
    record = await generations_repo.create(
        db,
        # Always text_to_image for now, even when a photo is attached: the
        # pipeline turns the photo into a text description and generates from
        # scratch (CH-19). Labelling it image_to_image would make the field lie,
        # and it is the field pricing and analytics will be read from.
        operation="text_to_image",
        user_id=user.id,
        status="running",
        prompt=body.prompt,
        request_params={
            "tile_id": body.tile_id,
            "answers": body.answers,
            "style": body.style,
            "photo_media_id": _media_id(body.photo_url),
            "type": gen_type.value,
        },
        source_media_id=_media_id(body.photo_url),
        cost=cost,
    )

    try:
        result_url, prompt = await content_gen.generate(
            gen_type=gen_type,
            tile=tile,
            answers=body.answers,
            free_text=body.prompt,
            style=body.style,
            photo_url=photo_data_url,
        )
    except content_gen.GenerationUnavailable:
        # Upstream generator unavailable — refund and tell the client to retry.
        await wallet.cancel(db, user.id, payment)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Image generator is busy right now, your TEKI was refunded — please try again.",
        )
    except Exception:
        logger.exception("Generation failed for user %s (tile=%s)", user.id, body.tile_id)
        await wallet.cancel(db, user.id, payment)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail="Generation failed")

    await wallet.confirm(db, payment)

    asset = await _store_result(db, user.id, result_url)
    await generations_repo.mark_done(db, record, result_media_id=asset.id, prompt=prompt)
    generation_id = record.id
    result_url = f"/api/media/{asset.id}"

    if chat is not None:
        message = ChatMessage(
            id=new_id("msg_"),
            role=ChatRole.AI,
            generated_img=result_url,
            generation_id=generation_id,
        )
        await chat_service.add_message(chat, message)

    balance = await wallet.get_balance(db, user.id)
    return GenerateResponse(
        id=generation_id,
        url=result_url,
        type=gen_type,
        balance=balance.available,
        prompt=prompt,
    )


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _media_id(photo_url: str | None) -> str | None:
    """Pull the media id out of ``/api/media/med_…``; ignore anything else."""
    if not photo_url:
        return None
    marker = "/api/media/"
    if marker in photo_url:
        return photo_url.split(marker, 1)[1].split("?", 1)[0].strip("/")
    return None


async def _resolve_photo(db: AsyncSession, user_id: str, photo_url: str | None) -> str | None:
    """Turn a stored photo into an inline data URL for the generator."""
    media_id = _media_id(photo_url)
    if media_id is None:
        return photo_url  # already a data: URL or an external link
    asset = await db.get(MediaAsset, media_id)
    if asset is None or asset.user_id != user_id or asset.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Photo not found")
    data = await get_storage().get(asset.storage_key)
    if data is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Photo not found")
    return f"data:{asset.mime or 'image/jpeg'};base64,{base64.b64encode(data).decode()}"


async def _store_result(db: AsyncSession, user_id: str, result_url: str):
    """Move a freshly generated file from the working directory into storage.

    The generator still writes to ``uploads/`` internally; this is the seam that
    gets the result into the private bucket and out of a served directory. When
    the generation core is rewritten (CH-21) providers will write here directly.
    """
    local = Path(result_url.lstrip("/")) if result_url.startswith("/uploads/") else None
    if local is None or not local.exists():
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail="Generation produced no file")
    data = local.read_bytes()
    asset = await media_repo.save_image(db, user_id=user_id, kind="generation", data=data)
    local.unlink(missing_ok=True)
    return asset
