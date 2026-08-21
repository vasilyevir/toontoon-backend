"""Generation endpoints: file upload + the two-phase generate flow."""
from __future__ import annotations

import logging
import re
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from app.config import settings
from app.core import rate_limit
from app.core.security import new_id
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session as get_db_session
from app.db.repositories import generations as generations_repo
from app.db.repositories import styles as styles_repo
from app.db.repositories import media as media_repo
from app.db.repositories import profiles as profiles_repo
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

# Сколько человек за раз можно привести в один кадр.
#
# Четверо — не ограничение реестра (референсов там берут по четырнадцать), а
# граница, за которой модели перестают держать лица врозь: чем больше людей,
# тем охотнее из них лепится общий усреднённый человек. Лучше честно не пустить
# пятого, чем показать компанию незнакомцев.
MAX_JOINT_PEOPLE = 4
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
    # Правка кадра или новая просьба — решают слова, а не то, что кадр уже есть.
    #
    # Приложение считает правкой любое следующее сообщение: «сделай фон ночным»
    # после готового постера — это правка, и так чаще всего и есть. Но «хочу
    # постер 16:9 в этой стилистике» — не правка: назван другой вид картинки,
    # другие пропорции. Прошлый запрос при этом уезжал целиком, вместе со своим
    # портретным назначением и фотографическим стилем, а новые слова
    # дописывались к нему хвостом — человек получал прежний кадр с припиской.
    refine_key = body.refine.value if body.refine else None
    refine_note = (body.refine_note or "").strip() or None
    restated = _restates_the_picture(refine_note) if refine_note else {}
    if restated:
        # Слова сказаны только что и глядя на кадр — они сильнее полей,
        # приехавших из прошлого запроса.
        free_text = " ".join(p for p in (free_text, refine_note) if p) or refine_note
        refine_key, refine_note = None, None

    intent = (restated.get("intent") or body.intent
              or (conversation.detect_intent(free_text) if free_text else None))
    style = restated.get("technique") or (None if restated else body.style)
    aspect = restated.get("format") or (None if restated else body.aspect)
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
    profile_extras: list[str] = []
    profile = None
    # Кого рисуем. Выбор человека сильнее основного профиля: он только что
    # сказал, про кого речь, — и сказал, возможно, про нескольких.
    picked = await _picked_profiles(db, user.id, body)
    # Имена людей в кадре по порядку их снимков. Пусто, пока человек один:
    # одному имя не нужно, ему нужно сходство.
    cast: list[str] = []
    # Выбранные вдвоём и больше — сами по себе повод подставить снимки: нажать
    # на двух человек и есть просьба нарисовать их вместе, других слов для этого
    # не нужно. Ждать вдобавок слова «меня» значило бы не замечать выбор.
    #
    # И человек не выпадает из кадра, когда просьба меняет его вид. «Хочу
    # постер 16:9 в этой стилистике» — про тот же кадр, в котором он только что
    # стоял; слова «меня» там нет, назначение сменилось на постер, и снимок
    # переставал подставляться вовсе. Приходил постер без человека — а на месте
    # человека модель придумывала кого-то своего.
    wants_people = (
        len(picked) > 1
        or intent in conversation.NEEDS_PHOTO
        or _asks_for_self(free_text)
        or (bool(restated) and await _last_frame_had_person(db, user.id))
    )
    if photo_url is None and wants_people:
        # Сначала профиль: он собран из снимков, которые человек использовал как
        # себя, и знает про него больше, чем последняя загрузка.
        profile = picked[0] if picked else await profiles_repo.ensure_silent_profile(db, user.id)
        if len(picked) > 1:
            media_ids, cast = _joint_references(picked)
        else:
            take = max(1, settings.profile_reference_count)
            media_ids = profiles_repo.references(profile, limit=take) if profile else []
        if media_ids:
            photo_url = f"/api/media/{media_ids[0]}"
            profile_extras = [f"/api/media/{mid}" for mid in media_ids[1:]]
            used_saved_photo = True
        else:
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
    for url in list(body.extra_photo_urls) + profile_extras:
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
    if photo is not None and not style_refs and not body.roles_chosen:
        # Профиль меняет саму постановку вопроса. Без него единственный
        # приложенный снимок — это человек, и разбирать нечего. С ним лицо у нас
        # уже есть, и приложенное чаще оказывается образцом: «сделай меня в
        # стилистике вот этого» — самая частая просьба, и раньше она требовала
        # от человека объяснять, где кто.
        known = picked[0] if picked else await profiles_repo.get_default(db, user.id)
        person_known = known is not None and bool(known.media_ids)
        if extra_photos or person_known:
            roles = await gpt_service.reference_roles(
                [photo, *extra_photos], free_text or "", person_known=person_known)
            if roles:
                everything = [photo, *extra_photos]
                people = [img for img, role in zip(everything, roles) if role == "person"]
                style_refs = [img for img, role in zip(everything, roles) if role == "style"]
                logger.info("Роли приложенных снимков: %s", ", ".join(roles))
                if people:
                    photo, extra_photos = people[0], people[1:]
                elif known is not None:
                    # Приложены одни образцы — людей берём из профилей. Это и
                    # есть «сделай нас с Аней в стилистике вот этого»: лица уже
                    # сохранены, приложить человек хотел только образец.
                    if len(picked) > 1:
                        from_profile, names = _joint_references(picked)
                    else:
                        take = max(1, settings.profile_reference_count)
                        from_profile = profiles_repo.references(known, limit=take)
                        names = []
                    loaded = [await _load_photo(db, user.id, f"/api/media/{mid}")
                              for mid in from_profile]
                    loaded = [img for img in loaded if img is not None]
                    if loaded:
                        photo, extra_photos = loaded[0], loaded[1:]
                        used_saved_photo = True
                        cast = names if len(loaded) == len(names) else []

    # Образец называет стиль сам.
    #
    # Без этого «сделай в такой же стилистике» уходило с фотографическим
    # якорем: техника не названа словами, значит по умолчанию фотография — и в
    # промпте оказывались рядом «hyperrealistic photographic render» и «скопируй
    # технику рисунка с образца». Якорь стоит первым и выигрывает; человек
    # прикладывает аниме-постер, а получает свою фотографию. Смотрим на образец
    # тем же зрением, каким разбираем роли: он показан картинкой, значит и
    # прочитать его надо глазами, а не ждать, что его перескажут словами.
    if style_refs and style is None:
        style = await gpt_service.style_of_sample(style_refs[0])
        if style:
            logger.info("Стиль образца: %s", style)

    # Буквы заказывают словами, а не назначением.
    #
    # Раньше надпись включало только «постер» или «открытка». «А вместо akai
    # напиши моё имя» на портрете значило: в системный промпт уходит «никаких
    # букв в кадре» — ровно наоборот сказанному.
    poster = (intent or "") in gpt_service.LETTERING_INTENTS

    # Какие именно слова набрать.
    #
    # Без этого «постер» включал надпись, а слов для неё не было — и модель
    # набирала первое, что видела: собственную просьбу человека. На кадре
    # оказывалось «I WANT TO SEE POSTER 16:9 IN THIS STYLES» плакатным
    # шрифтом. Буквы включаем только когда есть что набрать; иначе постер
    # остаётся постером, но с чистым местом под заголовок.
    lettering_text = (body.answers or {}).get("text")
    if not lettering_text and free_text and (poster or _wants_lettering(free_text)):
        said = await gpt_service.extract_slots(free_text, ["text"])
        lettering_text = said.get("text")
    lettering = bool(lettering_text)

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
                subject=profile.kind if profile is not None else "person",
                cast=cast, lettering=lettering, poster=poster,
                lettering_text=lettering_text,
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
    if refine_key is not None or refine_note:
        prompt, prefer_refine = prompt_style.refine(prompt, refine_key, refine_note)

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
            "refine": refine_key,
            "lettering": lettering,
            "lettering_text": lettering_text,
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
            # нужен и тем и другим. Как и просьбе про буквы, сказанной словами:
            # исполнитель выбирается под то, что в кадре должно появиться, а не
            # под то, как человек это назвал.
            or (prompt_style.LETTERING_PROVIDER if lettering else None)
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
    r"\b(me|myself|us|my (photo|face|picture))\b|меня|себя|нас\b|вдво[её]м"
    r"|мо[её] (фото|лицо)",
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


# Пропорции, названные словами. Цифрами человек пишет чаще, чем словами, но
# пишет и так: «горизонтальный», «квадрат».
_ASPECT_WORDS: tuple[tuple[str, str], ...] = (
    (r"16\s*[:xх]\s*9|landscape|horizontal|широк|горизонтал", "16:9"),
    (r"9\s*[:xх]\s*16|vertical|сторис|вертикал", "9:16"),
    (r"4\s*[:xх]\s*5|portrait format|книжн", "4:5"),
    (r"1\s*[:xх]\s*1|square|квадрат", "1:1"),
)

# Техника, названная словами. Список короткий намеренно: это не разбор просьбы,
# а признак того, что человек передумал про сам вид картинки.
_TECHNIQUE_WORDS: tuple[tuple[str, str], ...] = (
    (r"anime|аниме|манг", "anime"),
    (r"3d cartoon|3д мультф|мультфильм|cartoon", "3d_cartoon"),
    (r"photo|фото|реалист|realistic", "realistic"),
)

# Просьба про буквы. «Напиши моё имя вместо akai» — это заказ надписи, и без
# него в промпт уходит прямо противоположное: «никаких букв в кадре».
#
# Ошибка здесь тихая и полная: человек получает красивую картинку без
# единственного, ради чего он её и заказывал.
_LETTERING_RE = re.compile(
    r"напиш|надпис|подпис|\bтекст|буквам|заголов|"
    r"\b(write|text|caption|title|lettering|headline|says?)\b",
    re.IGNORECASE,
)


def _wants_lettering(text: str | None) -> bool:
    return bool(text and _LETTERING_RE.search(text))


def _restates_the_picture(text: str) -> dict[str, str]:
    """Названо ли в словах другое: вид картинки, пропорции, техника.

    Отличает новую просьбу от правки кадра. «Сделай фон ночным» — правка: тот
    же кадр, другая деталь. «Хочу постер 16:9 в этой стилистике» — не правка:
    человек назвал другой вид картинки, и достраивать его к прошлому промпту
    значит выдать ему прошлый кадр с припиской.
    """
    low = text.lower()
    found: dict[str, str] = {}
    intent = conversation.explicit_intent(low)
    if intent:
        found["intent"] = intent
    for pattern, value in _ASPECT_WORDS:
        if re.search(pattern, low, re.IGNORECASE):
            found["format"] = value
            break
    for pattern, value in _TECHNIQUE_WORDS:
        if re.search(pattern, low, re.IGNORECASE):
            found["technique"] = value
            break
    return found


async def _last_frame_had_person(db: AsyncSession, user_id: str) -> bool:
    """Стоял ли человек в кадре, который сейчас переделывают.

    Спрашиваем базу, а не догадываемся по словам: «хочу это постером» ничего не
    говорит о людях, но говорит «это» — а в том кадре человек был, и выкинуть
    его из следующего значит не понять просьбу целиком.
    """
    stmt = (
        select(m.Generation.source_media_id)
        .where(m.Generation.user_id == user_id)
        .order_by(m.Generation.created_at.desc())
        .limit(1)
    )
    return bool(await db.scalar(stmt))


async def _picked_profiles(
    db: AsyncSession, user_id: str, body: GenerateRequest
) -> list[m.PersonProfile]:
    """Профили, которые человек выбрал сам, в том порядке, в каком выбрал.

    Порядок — не мелочь: он станет порядком референсов, а по нему модель и
    поймёт, кто из них кто. Старое одиночное поле остаётся рабочим: сборки, не
    знающие про совместные кадры, продолжают присылать одного человека.
    """
    ids = list(body.profile_ids)
    if body.profile_id and body.profile_id not in ids:
        ids.append(body.profile_id)

    found: list[m.PersonProfile] = []
    for profile_id in ids[:MAX_JOINT_PEOPLE]:
        profile = await profiles_repo.get(db, profile_id, user_id=user_id)
        if profile is not None:
            found.append(profile)
    return found


def _joint_references(picked: list[m.PersonProfile]) -> tuple[list[str], list[str]]:
    """Снимки и имена для совместного кадра — по одному снимку на человека.

    По одному, а не по три: промпт говорит «первый снимок — Никита, второй —
    Аня», и это правда ровно до тех пор, пока у каждого по кадру. Дать по три
    значит сломать нумерацию, а вместе с ней и единственное, что удерживает
    модель от общего усреднённого лица на двоих.
    """
    pairs = [(p.name, profiles_repo.references(p, limit=1)) for p in picked]
    media_ids = [ids[0] for _, ids in pairs if ids]
    names = [name for name, ids in pairs if ids]
    return media_ids, (names if len(names) > 1 else [])


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
