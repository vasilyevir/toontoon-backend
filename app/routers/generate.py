"""Generation endpoints: file upload + the two-phase generate flow."""
from __future__ import annotations

import logging
import re
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from app.config import settings
from app.core import rate_limit
from app.core.security import new_id
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session as get_db_session
from app.db.repositories import generations as generations_repo
from app.db.repositories import styles as styles_repo
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
from app.services import content_gen, conversation, generations_service, prompt_style, tiles_data, video_gen, wallet
from app.services import gpt as gpt_service

# Пропорции, которые принимают все подключённые исполнители. Вертикаль первой:
# продукт мобильный, и она же остаётся значением по умолчанию.
ASPECTS = {"9:16", "4:5", "1:1", "16:9"}
from app.services import generation as generation_core
from app.services.generation import registry as _registry
generation_core.registry = _registry

logger = logging.getLogger("toontoon.generate")

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

    1. reserve TOONTOON (create payment)
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

    style_row = await styles_repo.get(db, body.style_id) if body.style_id else None
    if body.style_id and style_row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Unknown style")
    # Стиль из витрины обещает конкретный результат, и обещание держится
    # фотографией: без неё это будет другая картинка, а человек уже заплатил.
    if style_row is not None and (style_row.input_spec or {}).get("needs_photo") and not body.photo_url:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="This style needs your photo.",
        )

    # Cost is driven by the tile (videos cost more) or the requested type.
    if tile is not None:
        tile_is_video = tile.category.value == "video"
        # Reject a request whose declared type contradicts the tile's category
        # BEFORE reserving TOONTOON, so a mismatch never charges the wrong amount
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
    elif style_row is not None:
        # Цена написана на карточке до нажатия — она и списывается.
        gen_type = GenerationType.IMAGE
        cost = style_row.cost
    else:
        gen_type = body.type
        cost = settings.video_toontoon_cost if gen_type == GenerationType.VIDEO else settings.image_toontoon_cost

    # Просьба из чата — это весь разговор, а не последняя реплика.
    #
    # Приложение слало то, что человек напечатал последним, и всё остальное
    # пропадало: имя, цвета, слова для постера, ссылка на баскетбол — каждый
    # ответ на вопрос перезаписывал предыдущий. На «постер в стиле аниме,
    # бело-сине-красный, как для NBA» после пяти уточнений в модель уезжало
    # «на фоне хочу интересные шрифты, а не места» — и приходил кадр, к
    # которому весь разговор отношения не имел.
    #
    # Собирается это здесь, потому что разговор лежит здесь. Приложение помнит
    # только свой экран, а сервер — всё, что человек сказал с последней
    # «Очистки».
    #
    # Пустой `prompt` из чата и означает «собери сам». Ведомый разговор,
    # наоборот, присылает собранное — там просьба сложена из ответов по полям, и
    # подмешивать к ней сырой тред значило бы сказать всё дважды.
    free_text = body.prompt
    if body.from_chat and not (body.prompt or "").strip():
        history = await chat_repo.context_messages(
            db, user, limit=settings.chat_context_messages
        )
        said = " ".join(
            msg["content"] for msg in history
            if msg.get("role") == "user" and msg.get("content")
        )
        free_text = " ".join(p for p in (said, body.prompt) if p and p.strip()).strip() or None

    # Что человек сказал словами, тем и делаем.
    #
    # Раньше назначение, стиль и пропорции доезжали только если приложение
    # положило их в поля — то есть если человек прошёл разговор с кнопками. Но
    # он их не выбирает, он описывает: пишет «постер в стилистике этого», жмёт
    # отправить, и всё сказанное пропадает. В запросе тогда пусто, и сервер
    # честно делает умолчание — фотографический якорь вместо постера.
    #
    # Поэтому слова разбираются здесь, всегда, каким бы путём запрос ни пришёл.
    # Явно присланное сильнее: оно уже результат выбора человека, а не догадки
    # о его фразе.
    intent = body.intent or (conversation.detect_intent(free_text) if free_text else None)
    style, aspect = body.style, body.aspect
    unknown = [
        slot for slot, value in (("technique", style), ("format", aspect))
        if value is None
    ]
    if free_text and unknown:
        said = await gpt_service.extract_slots(free_text, unknown)
        style = style or said.get("technique")
        aspect = aspect or said.get("format")

    # Запрос без темы — не запрос, а осечка приложения. Проверяем ДО
    # резервирования, как и всё остальное: за такую генерацию нельзя брать
    # деньги, потому что нарисовать по ней нечего.
    #
    # Так однажды и вышло: провайдер отказал по сети, приложение стёрло
    # собранное человеком и следующим нажатием отправило пустоту. Сборщик
    # промптов честно придумал за него «тёплую дружелюбную картинку», дешёвый
    # запасной исполнитель это нарисовал, и человек получил фотографию двух
    # деревянных яиц вместо постера — за свой TOONTOON.
    if not _has_subject(body, free_text):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="Tell us what to draw first — a few words are enough.",
        )

    # Видео вне скоупа первой версии. Проверяем ДО резервирования, чтобы отказ
    # не проходил через кошелёк вообще.
    if gen_type == GenerationType.VIDEO and not settings.video_enabled:
        raise HTTPException(
            status.HTTP_501_NOT_IMPLEMENTED,
            detail="Video generation is not available in this version.",
        )

    reason = f"toontoon:{gen_type.value}_generate"
    # Note on refunds in this handler: the request runs inside one database
    # transaction, and raising rolls it back — so a failed image generation
    # leaves no charge at all, rather than a charge plus a refund. The explicit
    # cancel() calls below are kept because they are the correct behaviour in
    # any path that does NOT roll back (the video worker, which commits the
    # charge before the job starts), and because they are idempotent.
    try:
        payment = await wallet.reserve(db, user.id, amount=cost, reason=reason)
    except wallet.InsufficientFunds:
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, detail="Not enough TOONTOON")

    # ── Video: long-running (keyframes → Seedance, ~4–5 min). Run as a background
    # job and let the client poll GET /api/generations/{id}. We persist a QUEUED
    # record now; the worker flips it to DONE (with the URL) or FAILED (+ refund).
    if gen_type == GenerationType.VIDEO:
        if not settings.kie_enabled:
            await wallet.cancel(db, user.id, payment)
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Video generator is not configured. Your TOONTOON was refunded.",
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
            style=style,
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
    photo_url = body.photo_url
    used_saved_photo = False
    # Человек в кадре нужен, а снимка нет — берём тот, что он уже использовал.
    #
    # Вопрос «приложите фотографию» и три касания выбора стоят дороже всего в
    # этом пути, а ответ мы знаем: тот же снимок, что и в прошлый раз. Молча
    # так делать нельзя — своё лицо там, где его не ждали, чувствительнее любой
    # другой ошибки, — поэтому ответ говорит приложению, что снимок подставлен,
    # и оно скажет об этом человеку.
    if photo_url is None and (intent in conversation.NEEDS_PHOTO or _asks_for_self(free_text)):
        saved = await media_repo.last_person_photo(db, user.id)
        if saved is not None:
            photo_url = f"/api/media/{saved.id}"
            used_saved_photo = True

    photo = await _load_photo(db, user.id, photo_url)
    redraw = await _is_own_work(db, photo_url)
    # Остальные снимки — тем же путём. Порядок сохраняем: модели связывают
    # референсы с упоминаниями в промпте по очереди, и перестановка меняет, кто
    # в кадре кем окажется.
    extra_photos = []
    for url in body.extra_photo_urls:
        loaded = await _load_photo(db, user.id, url)
        if loaded is not None:
            extra_photos.append(loaded)

    # Образцы стиля идут последними и после всех людей.
    #
    # Порядок здесь не оформление: модели связывают референсы с упоминаниями в
    # промпте по очереди, а требование «последняя картинка — это образец, а не
    # человек» стоит в промпте буквально. Поставить образец первым значит
    # сказать модели, что человек в кадре — тот, кто нарисован на постере.
    style_refs = []
    for url in body.style_ref_urls:
        loaded = await _load_photo(db, user.id, url)
        if loaded is not None:
            style_refs.append(loaded)

    # Роли не назначены — разбираем сами, по картинкам и по словам.
    #
    # Человек прикладывает своё фото и постер и пишет «сделай меня в стилистике
    # этого». По его словам роли ясны, по порядку файлов — нет, а требовать
    # расставить их переключателями значит требовать понимать нашу механику.
    # Он описывает, что хочет получить, и это правильно: разбираться, где чьё
    # лицо, — наша работа, а не его.
    #
    # Ошибка тут дороже всех: постер, принятый за человека, — это чужое лицо в
    # кадре вместо своего. Ровно так и вышло на первом же таком запросе.
    #
    # Но если человек роли расставил сам — не лезем. «Оба снимка люди» это
    # законная просьба (двое в кадре), и отличить её от «ничего не выбирал»
    # можно только по этому флагу: роль человека стоит умолчанием.
    if photo is not None and not style_refs and extra_photos and not body.roles_chosen:
        roles = await gpt_service.reference_roles([photo, *extra_photos], free_text or "")
        if roles:
            everything = [photo, *extra_photos]
            people = [img for img, role in zip(everything, roles) if role == "person"]
            style_refs = [img for img, role in zip(everything, roles) if role == "style"]
            photo, extra_photos = people[0], people[1:]
            logger.info("Роли приложенных снимков: %s", ", ".join(roles))

    extra_photos.extend(style_refs)

    # Операция решается ДО сборки промпта, а не после: редактированию нужен
    # текст другого жанра — инструкция «сохрани человека, помести в такую-то
    # сцену» вместо описания сцены с нуля. Собрать промпт, а потом узнать, что
    # он поедет на фото-путь, значит отправить модели описание чужой картинки.
    operation = generation_core.Operation.TEXT_TO_IMAGE
    if photo is not None:
        can_edit = await generation_core.registry.candidates(
            db, generation_core.Operation.IMAGE_TO_IMAGE
        )
        if can_edit:
            operation = generation_core.Operation.IMAGE_TO_IMAGE
    editing = operation is generation_core.Operation.IMAGE_TO_IMAGE

    try:
        if style_row is not None:
            # Промпт стиля написан руками и проверен глазами на примере из
            # каталога. Отдавать его GPT на переписывание значит показывать
            # одно, а генерировать другое.
            prompt, negative = content_gen.build_style_prompt(style_row, editing=editing)
        else:
            prompt, negative = await content_gen.build_prompt_for(
                tile=tile, answers=body.answers, free_text=free_text, style=style,
                editing=editing, intent=intent,
                style_ref=bool(style_refs), redraw=redraw,
            )
    except content_gen.PromptUnavailable:
        # Переводить запрос нечем. Отказ с возвратом — единственный честный
        # ответ: картинка, собранная из непереведённого текста, к просьбе
        # отношения не имеет, а списание за неё выглядит как обман.
        await wallet.cancel(db, user.id, payment)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="We couldn't process your request right now, your TOONTOON was refunded — please try again in a minute.",
        )


    # Уточнение к предыдущему кадру дописывается последним — после того, как
    # промпт стиля собран целиком. Поставить его раньше значило бы дать модели
    # прочитать «сделай рисованнее» до того, как она узнала, что рисовать.
    prefer_refine = None
    if body.refine is not None or body.refine_note:
        prompt, prefer_refine = prompt_style.refine(
            prompt,
            body.refine.value if body.refine else None,
            body.refine_note,
        )

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
            "style_id": body.style_id,
            "answers": body.answers,
            "aspect": aspect,
            "style": style,
            "intent": intent,
            "style_refs": len(body.style_ref_urls),
            "photo_media_id": _media_id(photo_url),
            "used_saved_photo": used_saved_photo,
            "redraw": redraw,
            "type": gen_type.value,
            "refine": body.refine.value if body.refine else None,
        },
        source_media_id=_media_id(photo_url),
        cost=cost,
    )

    request = generation_core.GenerationRequest(
        operation=operation,
        prompt=prompt,
        negative_prompt=negative,
        image=photo[0] if photo else None,
        image_mime=photo[1] if photo else None,
        extra_images=extra_photos,
        # Пропорции сверяем со списком, а не пропускаем как есть: значение
        # уезжает вендору, и произвольная строка оттуда вернётся отказом,
        # который человек увидит как нашу поломку.
        params={"aspect": aspect} if aspect in ASPECTS else {},
    )

    try:
        result = await generation_core.run(
            db, request,
            # Уточнение перебивает предпочтение стиля: человек только что
            # посмотрел на кадр и сказал, что не так, — это более свежий довод,
            # чем выбор, записанный в каталоге месяц назад.
            # Порядок доводов: свежее — сильнее. Уточнение человек дал только
            # что, глядя на кадр; назначение он выбрал в начале разговора;
            # предпочтение стиля записано в каталоге месяц назад.
            prefer=prefer_refine
            # Назначение и стиль смотрятся оба: новые сборки шлют «poster» в
            # `intent`, старые — в `style`, а маршрут к тому, кто умеет буквы,
            # нужен и тем и другим.
            or prompt_style.preferred_provider(intent)
            or prompt_style.preferred_provider(style)
            or ((style_row.prompt_template or {}).get("provider") if style_row else None),
        )
    except generation_core.GenerationUnavailable as exc:
        await _record_failure(record, str(exc))
        await wallet.cancel(db, user.id, payment)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Image generator is busy right now, your TOONTOON was refunded — please try again.",
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

    # В переписку попадает только то, что из неё и вышло. Кадр, запущенный с
    # карточки на главной, там был бы репликой без вопроса — картинкой посреди
    # разговора, которого не было. Он и так лежит в истории работ, где его и
    # ищут.
    if body.from_chat:
        # Что именно человек попросил на этот раз: уточнение, если оно было, —
        # иначе исходная просьба. При правке кадра в треде должно стоять
        # «сделай фон ночным», а не промпт двухчасовой давности.
        said = (body.refine_note or body.prompt or "").strip()
        if body.post_prompt and said:
            await chat_repo.add_message(db, user_id=user.id, role="user", content=said)
        await chat_repo.add_message(
            db, user_id=user.id, role="assistant", generation_id=generation_id
        )

    balance = await wallet.get_balance(db, user.id)
    return GenerateResponse(
        used_saved_photo=used_saved_photo,
        id=generation_id,
        url=result_url,
        type=gen_type,
        balance=balance.available,
        prompt=prompt,
    )


# ─── Helpers ─────────────────────────────────────────────────────────────────


# Слова, которыми просят себя в кадре. Список короткий и намеренно грубый:
# ошибка здесь стоит одной лишней подстановки снимка, который и так лежит.
_SELF_WORDS = re.compile(
    r"\b(me|myself|my (photo|face|picture))\b|меня|себя|мо[её] (фото|лицо)",
    re.IGNORECASE,
)


def _asks_for_self(text: str | None) -> bool:
    return bool(text and _SELF_WORDS.search(text))


def _has_subject(body: GenerateRequest, free_text: str | None = None) -> bool:
    """Есть ли в запросе то, что вообще нужно нарисовать.

    Три законных источника темы: плитка, стиль из каталога и слова человека.
    Слова — это либо `prompt`, либо собранный разговор: из чата приложение
    присылает пустой промпт, а сказанное лежит в треде.

    Одной фотографии мало — по ней непонятно, что с ней делать; путь «улучшить
    снимок как есть» это отдельная операция со своим текстом.
    """
    words = free_text if free_text is not None else body.prompt
    # Образец стиля — тоже тема: «сделай как здесь» можно сказать картинкой, и
    # слов при этом не требуется вовсе.
    return bool(body.tile_id or body.style_id or body.style_ref_urls
                or (words or "").strip())


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


async def _is_own_work(db: AsyncSession, photo_url: str | None) -> bool:
    """Это наша же прошлая работа, а не снимок человека.

    Знание бесплатное — оно записано в самом файле, — и меняет всё: к рисунку
    нельзя применять требование «сохрани лицо с фотографии», а выбросить его
    как чужой образец тем более нельзя. Человек сказал «вот этот кадр, но
    поменяй фон» — значит правим этот кадр.
    """
    media_id = _media_id(photo_url)
    if media_id is None:
        return False
    asset = await db.get(MediaAsset, media_id)
    return asset is not None and asset.kind == "generation"


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
