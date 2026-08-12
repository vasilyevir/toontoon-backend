"""Generation endpoints: file upload + the two-phase generate flow."""
from __future__ import annotations

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
from app.db import models as m
from app.db.models import MediaAsset
from app.storage import get_storage
from app.deps import Context, required_context
from app.models.generation import (
    Generation,
    GenerateRequest,
    GenerateResponse,
    GenerationStatus,
    GenerationType,
)
from app.db.repositories import chat as chat_repo
from app.services import content_gen, generations_service, tiles_data, video_gen, wallet
from app.services import generation as generation_core
from app.services.generation import registry as _registry
generation_core.registry = _registry

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

    # Видео вне скоупа первой версии. Проверяем ДО резервирования, чтобы отказ
    # не проходил через кошелёк вообще.
    if gen_type == GenerationType.VIDEO and not settings.video_enabled:
        raise HTTPException(
            status.HTTP_501_NOT_IMPLEMENTED,
            detail="Video generation is not available in this version.",
        )

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

        balance = await wallet.get_balance(db, user.id)
        return GenerateResponse(
            id=generation.id,
            url="",
            type=GenerationType.VIDEO,
            balance=balance.available,
            prompt=generation.prompt,
            status=GenerationStatus.QUEUED,
        )

    # The reference photo lives in private storage and is handed over as bytes,
    # not as a URL a provider could fetch: nothing about someone's face should
    # be reachable by a link.
    photo = await _load_photo(db, user.id, body.photo_url)

    # The prompt is still built the old way (tile answers + GPT); only the part
    # that talks to a model has moved into the generation core.
    prompt, negative = await content_gen.build_prompt_for(
        tile=tile, answers=body.answers, free_text=body.prompt, style=body.style,
        photo_description=None,
    )

    # Which operation this actually is — decided by what we were given, not by
    # what we would like it to be. With a photo it is a real image_to_image only
    # if a provider is enabled for it; otherwise it is honest text_to_image.
    operation = generation_core.Operation.TEXT_TO_IMAGE
    if photo is not None:
        can_edit = await generation_core.registry.candidates(
            db, generation_core.Operation.IMAGE_TO_IMAGE
        )
        if can_edit:
            operation = generation_core.Operation.IMAGE_TO_IMAGE

    # The attempt is recorded before the provider is called, so a failure leaves
    # a trace instead of nothing at all.
    record = await generations_repo.create(
        db,
        operation=operation.value,
        user_id=user.id,
        status="running",
        prompt=prompt,
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

    request = generation_core.GenerationRequest(
        operation=operation,
        prompt=prompt,
        negative_prompt=negative,
        image=photo[0] if photo else None,
        image_mime=photo[1] if photo else None,
    )

    try:
        result = await generation_core.run(db, request)
    except generation_core.GenerationUnavailable as exc:
        await _record_failure(record, str(exc))
        await wallet.cancel(db, user.id, payment)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Image generator is busy right now, your TEKI was refunded — please try again.",
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Generation failed for user %s (tile=%s)", user.id, body.tile_id)
        await _record_failure(record, repr(exc))
        await wallet.cancel(db, user.id, payment)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail="Generation failed")

    await wallet.confirm(db, payment)

    asset = await media_repo.save_image(db, user_id=user.id, kind="generation", data=result.data)
    record.provider_id = result.provider_id
    record.provider_model = result.model
    await generations_repo.mark_done(db, record, result_media_id=asset.id, prompt=prompt)
    generation_id = record.id
    result_url = f"/api/media/{asset.id}"

    # The result belongs in the thread: there is only one, so nothing to choose.
    await chat_repo.add_message(
        db, user_id=user.id, role="assistant", generation_id=generation_id
    )

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


async def _load_photo(
    db: AsyncSession, user_id: str, photo_url: str | None
) -> tuple[bytes, str] | None:
    """Read the stored reference photo. Returns ``(bytes, mime)``."""
    media_id = _media_id(photo_url)
    if media_id is None:
        return None
    asset = await db.get(MediaAsset, media_id)
    if asset is None or asset.user_id != user_id or asset.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Photo not found")
    data = await get_storage().get(asset.storage_key)
    if data is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Photo not found")
    return data, asset.mime or "image/jpeg"


async def _record_failure(record, error: str) -> None:
    """Записать неудачную попытку в ОТДЕЛЬНОЙ транзакции.

    Иначе следа не остаётся: запрос падает, транзакция откатывается и вместе
    с ней исчезает запись о том, что человек вообще пытался. Для баланса откат
    — то что нужно (никто не списан), а для разбора жалобы «нажал и ничего
    не произошло» нужен именно этот след.
    """
    from app.db.session import session_scope

    try:
        async with session_scope() as db:
            failed = m.Generation(
                user_id=record.user_id,
                operation=record.operation,
                status="failed",
                prompt=record.prompt,
                request_params=record.request_params,
                source_media_id=record.source_media_id,
                cost=0,  # ноль: списания не было, откат его снял
                error=error[:2000],
            )
            db.add(failed)
    except Exception:  # noqa: BLE001 — диагностика не должна ломать ответ
        logger.exception("Не удалось записать неудачную генерацию")
