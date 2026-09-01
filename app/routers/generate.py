"""Generation endpoints: file upload + the two-phase generate flow."""
from __future__ import annotations

import logging
import re
from dataclasses import replace
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
from app.storage import images
from app.deps import Context, one_generation_at_a_time, required_context
from app.models.generation import (
    Generation,
    GenerateRequest,
    GenerateResponse,
    GenerationStatus,
    GenerationType,
)
from app.db.repositories import chat as chat_repo
from app.db.repositories import state as state_repo
from app.services import content_gen, conversation, generations_service, image_job, prompt_style, tiles_data, video_gen, wallet
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

    Тип из заголовка проверяется по-прежнему, но полагаться на него нельзя:
    его пишет клиент, а декодер выбирается по содержимому. Настоящую границу
    держит `images.ALLOWED_FORMATS` при разборе — там названы те же четыре
    формата, и совпадение двух списков проверяется тестом.
    """
    user, _ = ctx

    allowed, _remaining = await rate_limit.hit(
        f"upload:{user.id}", settings.uploads_per_hour, 3600
    )
    if not allowed:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                            detail="Too many uploads, try later")

    if file.content_type not in _ALLOWED_IMAGE_TYPES:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="Unsupported image type")

    # Потолок ДО чтения: тело уезжает в память целиком, и без него запрос на
    # полгигабайта — это полгигабайта памяти пода, ещё до того как его увидит
    # разбор картинки.
    #
    # Заголовку про длину верим ровно настолько, чтобы отказать раньше чтения;
    # соврать в нём можно, поэтому ниже стоит вторая проверка — уже по факту.
    заявлено = file.size if file.size is not None else None
    if заявлено is not None and заявлено > settings.max_upload_bytes:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            detail=f"Файл больше {settings.max_upload_mb} МБ")

    data = await file.read(settings.max_upload_bytes + 1)
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            detail=f"Файл больше {settings.max_upload_mb} МБ")
    try:
        asset = await media_repo.save_image(db, user_id=user.id, kind="upload", data=data)
    except Exception as exc:  # noqa: BLE001 — a broken image is a client error
        logger.warning("Upload rejected for user %s: %r", user.id, exc)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Could not read this image")

    return {"id": asset.id, "url": f"/api/media/{asset.id}"}


@router.post("/generate", response_model=GenerateResponse)
async def generate(
    body: GenerateRequest,
    ctx: Context = Depends(one_generation_at_a_time),
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

    # Это уже заказывали? Тогда отдать тот же заказ, а не завести второй.
    #
    # Кадр рисуется в фоне, а запрос возвращается сразу — и приложение, не
    # дождавшееся ответа на плохой связи, посылает то же самое заново. До ключа
    # повтор был неотличим от нового заказа и стоил ещё пятнадцать монет; ровно
    # об этом говорит комментарий у `image_job.schedule` ниже.
    #
    # ДО всех проверок и до кошелька: повтор не должен зависеть от того, что мы
    # думаем про его содержимое сейчас. Заказ уже принят, отвечаем тем же.
    if body.idempotency_key:
        прежний = await generations_repo.get_by_idempotency_key(
            db, user_id=user.id, key=body.idempotency_key)
        if прежний is not None:
            logger.info("Повтор заказа по ключу %s — отдаём прежний %s",
                        body.idempotency_key, прежний.id)
            balance = await wallet.get_balance(db, user.id)
            return GenerateResponse(
                id=прежний.id,
                url="",
                type=GenerationType((прежний.request_params or {}).get("type", "image")),
                balance=balance.available,
                prompt=прежний.prompt or "",
                status=_как_состояние(прежний.status),
            )

    tile = tiles_data.get_tile(body.tile_id) if body.tile_id else None
    if body.tile_id and tile is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Unknown tile")

    style_row = await styles_repo.get(db, body.style_id) if body.style_id else None
    if body.style_id and style_row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Unknown style")
    # Стиль из витрины обещает конкретный результат, и обещание держится
    # лицом: без него это будет другая картинка, а человек уже заплатил.
    #
    # Лицо не обязано быть приложено сейчас. Человек мог отдать снимки один раз
    # в профиль — просить их снова на каждой карточке значит не помнить, что он
    # сделал. Поэтому проверяем не вложение, а наличие лица вообще.
    style_needs_photo = bool(style_row is not None
                             and (style_row.input_spec or {}).get("needs_photo"))
    if style_needs_photo and not body.photo_url and not await _has_face_on_file(db, user.id, body):
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
    remembered_intent, remembered = None, {}
    if body.from_chat and not (body.prompt or "").strip():
        # Окно режется по моменту, когда человек начал просьбу заново.
        #
        # Замена состояния чистит поля, но не переписку, а слова кадр берёт
        # именно оттуда. «Хочу постер про баскетбол» → «нет, лучше открытку на
        # новый год» давало новогоднюю открытку с баскетбольным мячом: строка
        # понятого при этом показывала верное, и это худший вид ошибки — мы
        # показываем, что поняли правильно, а кадр говорит обратное.
        history = await chat_repo.context_messages(
            db, user, limit=settings.chat_context_messages,
            since=await state_repo.restarted_at(db, user),
        )
        said = " ".join(
            msg["content"] for msg in history
            if msg.get("role") == "user" and msg.get("content")
        )
        # Окно кончается, а просьба — нет. Технику, палитру и назначение
        # человек называет первой фразой; через двадцать реплик её тут уже нет,
        # и кадр уезжал без них — по одному лишь хвосту разговора.
        remembered_intent, remembered = await state_repo.load(db, user)
        remembered = _for_the_frame(remembered)
        free_text = " ".join(
            p for p in (_carried_over(remembered, said), said, body.prompt)
            if p and p.strip()
        ).strip() or None

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

    # Порядок здесь — это порядок доверия, и он стоил одного испорченного кадра.
    #
    # Человек сказал «хочу постер про баскетбол», потом «нет, лучше открытку на
    # новый год». Разговор понял верно: в строке встало «Card · New Year», в
    # реплике — «создам открытку». А в генерацию уехал `poster`, и вот почему:
    #
    #   1. Приложение решает назначение ОДИН раз — на том ходу, где впервые
    #      узнало технику, — и больше не пересматривает. У него остался
    #      «постер» из первой фразы.
    #   2. Его догадка стояла выше памяти сервера, где лежало правильное
    #      «открытка».
    #
    # Память сервера теперь выше — но только для просьб из чата: там она и
    # собрана, по одной реплике за раз, в правильном порядке. В ведомом пути
    # `body.intent` — это нажатая человеком кнопка, а не догадка, и перебивать
    # её состоянием чужого экрана нельзя.
    #
    # `explicit_intent(free_text)` сюда не годится вовсе: в окне лежат обе фразы
    # вперемешку, и он вернёт то назначение, что первым стоит в словаре, а не
    # то, что человек сказал последним. Разбор по одной реплике этой беды не
    # знает — потому и держим состояние, а не перечитываем тред.
    intent = (restated.get("intent")
              or (remembered_intent if body.from_chat and not restated else None)
              or body.intent
              or (conversation.explicit_intent(free_text) if free_text else None)
              or (conversation.detect_intent(free_text) if free_text else None))
    style = (restated.get("technique") or (None if restated else body.style)
             or (None if restated else remembered.get("technique")))
    aspect = (restated.get("format") or (None if restated else body.aspect)
              or (None if restated else remembered.get("format")))
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

    # ── Заказ заводится здесь, до всего остального ────────────────────────────
    #
    # Раньше запись появлялась в самом конце, после сборки промпта, — и до тех
    # пор заказа не существовало ни в одном списке. Замер: семь секунд между
    # нажатием и тем моментом, когда сервер о заказе способен рассказать. Всё
    # это время деньги у человека уже списаны, а показать ему нечего: кто
    # закрыл приложение и открыл заново, попадал внутрь этого окна и видел
    # пустое место.
    #
    # Промпта здесь ещё нет, и это правильно: заказ — про то, что человек
    # заплатил и ждёт, а не про то, какими словами мы это переведём модели.
    # Слова допишутся ниже, к той же записи.
    #
    # Коммит сразу и явный. Без него запись не видна другим запросам до конца
    # обработки, то есть ровно те семь секунд и остаются. Вместе с ней
    # коммитится и списание — так и надо: деньги взяты, заказ записан, одним
    # движением.
    # Операция здесь предварительная. Какой она будет на самом деле — с нуля
    # или правкой снимка, — решится ниже, когда станет известно, есть ли лицо;
    # это подробность исполнения, а не свойство заказа. Человек заплатил и ждёт
    # независимо от того, каким путём мы будем рисовать. Уточняется там же, где
    # дописывается промпт.
    record = await generations_repo.create(
        db,
        operation=generation_core.Operation.TEXT_TO_IMAGE.value,
        user_id=user.id,
        status="running",
        request_params={"payment_id": payment.payment_id, "type": gen_type.value},
        cost=cost,
        idempotency_key=body.idempotency_key,
    )
    await db.commit()

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
        # Выбранный профиль — это и есть ответ на вопрос «кто в кадре»: так
        # подписан сам список. Раньше одного выбранного не хватало, и «постер:
        # я в форме Lakers» уходил без единого снимка — модель придумывала на
        # его месте незнакомого человека. Хуже этого в продукте про своё лицо
        # ничего нет.
        bool(picked)
        # Стиль из витрины, который обещает человека в кадре, — тоже просьба
        # про лицо, даже когда человек не сказал ни слова.
        or style_needs_photo
        # Назначение, в котором человек предполагается. Не NEEDS_PHOTO: тот
        # уже — он про то, кого стоит побеспокоить вопросом, а здесь речь про
        # лицо, которое у нас уже есть.
        or intent in conversation.ABOUT_A_PERSON
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
            take = _reference_take(style, style_row)
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
    # Человек просил себя, а лица так и не нашлось — ни приложенного, ни в
    # профиле. Разговор его об этом предупредил и кнопку не отнял: это его
    # выбор. Но выбор надо считать, иначе мы не узнаем, читают ли предупреждение
    # вообще, — а узнать это можно только по тому, сколько таких кадров человек
    # стирает сразу.
    made_without_face = photo_url is None and _asks_for_self(free_text)
    if made_without_face:
        logger.info("Кадр про человека делается без его лица")

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
    # Слова, написанные на образце: их нельзя переносить в кадр, и запретить их
    # можно только назвав. Читаются тем же зрением, что и техника.
    sample_brands: list[str] = []
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
                        from_profile = profiles_repo.references(
                            known, limit=_reference_take(style, style_row))
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
        style, sample_brands = await gpt_service.style_of_sample(style_refs[0])
        if style:
            logger.info("Стиль образца: %s", style)
        if sample_brands:
            logger.info("Чужие марки на образце: %s", ", ".join(sample_brands))

    # Форма кадра — тоже с образца, когда человек её не называл.
    #
    # Композицию мы у образца просили с самого начала, а форму брали из
    # умолчания: горизонтальный постер AKAI приезжал вертикальным `9:16`.
    # Разделить их нельзя — вертикальная обрезка ломает ровно ту плакатную
    # вёрстку, за которой человек и пришёл.
    #
    # Названное словами сильнее: «сделай 16:9» — это выбор, а образец лишь
    # показан.
    if style_refs and aspect is None:
        aspect = images.aspect_of(style_refs[0][0])
        if aspect:
            logger.info("Пропорции образца: %s", aspect)

    # То же и для стиля витрины: у него образец — собственный пример на
    # карточке, и форма кадра часть того же обещания, что палитра и свет.
    # Пока форму нести было негде, четыре карточки показывали квадрат, а
    # делали вертикаль.
    if aspect is None and style_row is not None:
        aspect = (style_row.prompt_template or {}).get("aspect")

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
                style_ref=bool(style_refs), sample_brands=sample_brands, redraw=redraw,
                subject=profile.kind if profile is not None else "person",
                cast=cast, lettering=lettering, poster=poster,
                lettering_text=lettering_text,
            )
    except content_gen.PromptUnavailable:
        # Переводить запрос нечем. Отказ с возвратом — единственный честный
        # ответ: картинка, собранная из непереведённого текста, к просьбе
        # отношения не имеет, а списание за неё выглядит как обман.
        #
        # Заказ помечается неудачей здесь же. Он заведён и закоммичен выше, и
        # без этой пометки остался бы висеть в «рисуется» до сверки — то есть
        # полчаса показывал бы человеку работу, которой не будет, при уже
        # возвращённых деньгах.
        await generations_repo.mark_failed(
            db, record, error="Промпт собрать нечем: перевод недоступен.")
        await wallet.cancel(db, user.id, payment)
        await db.commit()
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

    # Дописываем заказ, заведённый в начале. Не заводим второй: заказ один —
    # тот, за который человек заплатил, — а промпт и подробности просьбы это
    # его свойства, а не новая работа.
    record.operation = operation.value
    record.prompt = prompt
    record.request_params = {
        **record.request_params,
        # Чем заплачено. Не для истории, а чтобы работу и деньги за неё
        # можно было свести запросом: при неудаче деньги возвращает фоновая
        # задача, а если не вернула и она — узнать об этом иначе неоткуда.
        "payment_id": payment.payment_id,
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
        # Только когда это правда: лишний ключ в каждой записи стоит места
        # и читается как «мы про это думали», хотя думать было не о чем.
        **({"made_without_face": True} if made_without_face else {}),
    }
    record.source_media_id = _media_id(photo_url)
    await db.flush()

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

    # К тому, кто умеет буквы, уходит просьба с буквами — а не всякая, которую
    # назвали постером.
    #
    # Замер 24 августа: «сделай мне постер в аниме» без единого названного слова
    # вернулся с надписью «CITY HORIZON SUNSET PROTOCOL». Мы сами отправили
    # пустой постер к лучшему в наборе типографу и попросили не набирать букв —
    # он набрал. Маршрут теперь решают слова; назначение остаётся доводом лишь
    # у старых сборок, которые шлют его в поле стиля и про надпись не знают.
    prefer_used = (
        prefer_refine
        or (prompt_style.LETTERING_PROVIDER if lettering else None)
        # Рисованный кадр — тому, кто умеет рисовать. Это правило, а не
        # настройка каждого стиля: беда общая для всех рисованных путей, и
        # чинить её по одному стилю значит ждать жалобы на каждый.
        or (prompt_style.DRAWN_PROVIDER
            if _wants_drawing(style, style_row, editing) else None)
        or prompt_style.preferred_provider(style)
        or ((style_row.prompt_template or {}).get("provider") if style_row else None)
    )

    # Заказ принят — дальше человек не ждёт с открытым запросом.
    #
    # Кадр рисуется от сорока секунд до минуты, и всё это время запрос висел.
    # iOS усыпляет приложение через тридцать секунд в фоне и убивает висящий
    # запрос: свернул, ответил на уведомление, погасил экран — «ошибка
    # генерации». Сервер при этом работу доводил до конца, кадр ложился в
    # историю, TOONTOON списывался, а человек жал «Try again» и платил второй
    # раз за то же самое.
    #
    # Ровно так уже работало видео. Там это было вынужденно — пять минут не
    # переживёт никакой запрос; здесь оказалось нужно по той же причине, только
    # менее очевидно.
    image_job.schedule(
        gen_id=record.id,
        user_id=user.id,
        payment_id=payment.payment_id,
        payment_amount=cost,
        request=request,
        prompt=prompt,
        prefer=prefer_used,
        sample_brands=sample_brands,
        # Проверять кадр на «пришла фотография» стоит только там, где ждали
        # рисунок: стили каталога фотографические, и лишний повтор для них —
        # вдвое дольше и вдвое дороже без единой причины.
        check_drawn=bool(editing and photo is not None
                         and _wants_drawing(style, style_row, editing)),
        from_chat=body.from_chat,
        # Что именно человек попросил на этот раз: уточнение, если оно было, —
        # иначе исходная просьба.
        said=((body.refine_note or body.prompt or "").strip()
              if body.post_prompt else None) or None,
    )

    balance = await wallet.get_balance(db, user.id)
    return GenerateResponse(
        used_saved_photo=used_saved_photo,
        id=record.id,
        url="",
        type=gen_type,
        balance=balance.available,
        prompt=prompt,
        status=GenerationStatus.QUEUED,
    )


def _как_состояние(status: str) -> GenerationStatus:
    """Состояние строки — в то, что понимает приложение.

    `queued` и `running` для него одно и то же: кадр ещё не готов, надо
    спрашивать дальше. Разница между ними наша, внутренняя.
    """
    if status == "done":
        return GenerationStatus.DONE
    if status == "failed":
        return GenerationStatus.FAILED
    return GenerationStatus.QUEUED


# ─── Helpers ─────────────────────────────────────────────────────────────────


# «Я» отдельным словом — такая же просьба про лицо, как «меня». Границы слова
# обязательны: «я» встречается внутри доброй половины русских слов, и без них
# «Постер: я в форме Lakers» не совпадало ни с чем, а снимок не подставлялся.
#
# Сам список живёт в `gpt`: он нужен и разговору — сказать, что лица у нас нет.
_asks_for_self = gpt_service.asks_for_self


def _for_the_frame(remembered: dict[str, str]) -> dict[str, str]:
    """Из записанного — только то, что можно сказать картинке.

    «Ответил образец» — пометка для разговора: она закрывает вопрос про технику,
    когда человек показал пальцем вместо слов. В промпте та же строка стала бы
    требованием нарисовать приложенный файл.
    """
    return {
        field: value for field, value in remembered.items()
        if value and not value.startswith("from the attached")
    }


def _carried_over(remembered: dict[str, str], said: str) -> str | None:
    """Сказанное раньше, чего в окне разговора уже не осталось.

    Дописывается к просьбе, а не заменяет её: слова человека сильнее нашего
    пересказа. И только выпавшее — повторять то, что и так в окне, значит
    сказать одно и то же дважды и утяжелить этим сцену.
    """
    lost = [
        value for field, value in remembered.items()
        if field != "photo" and value.lower() not in said.lower()
    ]
    return ", ".join(lost) or None


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


def _reference_take(style: str | None, style_row=None) -> int:
    """Сколько снимков человека отдавать модели.

    Один — когда картинку рисуют. Замер 21 августа: тот же постер с профилем из
    трёх снимков дважды пришёл фотографией (во втором кадре вернулся даже фон
    из референса — парк с деревьями), с профилем из одного снимка дважды пришёл
    аниме-постером. Чем больше фотографий в запросе, тем увереннее редактор
    считает задачу правкой фотографии, а не рисованием заново.

    На фотографическом пути тоже один, но пришли к этому иначе. Там стояло три
    по вере — «лишние ракурсы помогают сходству», — и замер 25 августа это
    опроверг: тройка проиграла и одному, и пяти по медиане, среднему и числу
    провалов, не выиграв ни на одном из трёх лиц. Подробности и оговорка про
    порядок снимков — в `profile_reference_count`.

    Ветка сохраняется, хотя обе стороны сейчас дают одно и то же: она держит
    рисованный путь на единице независимо от того, что однажды поставят в
    настройку.

    Стиль каталога спрашиваем у него самого: у него поле `style` пустое, а
    техника лежит в шаблоне. Пока «пусто» значило «рисунок», фотографические
    стили витрины уезжали с одним референсом — то есть с худшим сходством, чем
    та же просьба, набранная словами.
    """
    if _wants_drawing(style, style_row, editing=True):
        return 1
    return max(1, settings.profile_reference_count)


def _wants_drawing(style: str | None, style_row, editing: bool) -> bool:
    """Ждём ли мы рисунок, а не фотографию.

    У стиля из каталога техника записана в его же шаблоне; у свободной просьбы —
    в поле `style`, а пустое поле там почти всегда означает рисованный якорь,
    который выберет сборщик по словам сцены.
    """
    if style_row is not None:
        template = style_row.prompt_template or {}
        key = template.get("anchor") or ("realistic" if editing else prompt_style.DEFAULT_STYLE)
        return prompt_style.is_drawn(key)
    return (style or "") != "realistic"


async def _has_face_on_file(db: AsyncSession, user_id: str, body: GenerateRequest) -> bool:
    """Есть ли чьё лицо взять, когда снимок не приложен.

    Порядок тот же, что и у подстановки: выбранный профиль, основной, последняя
    своя фотография. Проверка идёт до списания — отказать надо раньше, чем
    возьмутся деньги, а не после.
    """
    if await _picked_profiles(db, user_id, body):
        return True
    known = await profiles_repo.get_default(db, user_id)
    if known is not None and (known.reference_ids or known.media_ids):
        return True
    return await media_repo.last_person_photo(db, user_id) is not None


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
