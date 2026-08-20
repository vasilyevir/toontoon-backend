"""Chat — one continuous thread per user (CH-20).

Replaces the thread list (`/api/chats/*`): there is a single conversation, its
history is kept, and "Clear" starts a new context without deleting anything.
Finding an old result is the history tab's job, not the chat's.

Routes:
  POST /api/chat          — send a message, get the reply, both are stored
  GET  /api/chat/messages — a page of the thread, newest first
  POST /api/chat/clear    — start a new context, keep the transcript
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import models as m
from app.db.repositories import chat as chat_repo
from app.db.repositories import profiles as profiles_repo
from app.db.session import get_session as get_db_session
from app.storage import get_storage
from app.deps import Context, optional_context, required_context
from app.services import conversation
from app.services import gpt as gpt_service

router = APIRouter(prefix="/api", tags=["chat"])


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    # Пустой текст законен, если приложен снимок: фотография — это уже просьба.
    # Раньше поле требовало хотя бы один символ, и приложение сочиняло за
    # человека реплику «Here is my photo.» — в треде стояло то, чего он не писал,
    # а модель отвечала на выдуманное.
    message: str = Field(default="", max_length=2000)
    # Приложенный снимок. Строка переписки хранит его отдельно от текста.
    media_id: Optional[str] = None
    # Снимки, приложенные к этой просьбе и ещё не разобранные по ролям.
    #
    # Роли решаются здесь, а не в генерации: если мы не уверены, кто из них
    # человек, спросить надо ДО того, как списан TOONTOON, а не после.
    attachment_ids: list[str] = Field(default_factory=list, max_length=8)
    # Kept for compatibility with the current app build, but ignored: the
    # server owns the context now and takes it from the stored thread, so a
    # client can no longer decide what the model remembers.
    history: list[ChatMessage] = Field(default_factory=list, max_length=20)
    # Приложен ли снимок к этому запросу. Нужно, чтобы не напоминать про
    # фотографию тому, кто её только что приложил, и напомнить тому, кто забыл.
    photo_attached: bool = False
    # О чём в этом разговоре уже спрашивали. Приложение знает это точно — оно
    # получало ответ на каждый ход, — и присылает обратно, чтобы один и тот же
    # вопрос не повторялся. Без этого отказ запирает разговор: «без надписи»
    # слот не заполняет, и про надпись спрашивалось бы бесконечно.
    asked: list[str] = Field(default_factory=list, max_length=16)
    # Что человек уже выбрал руками: пропорции, техника. Приложение знает это
    # точно — он нажал кнопку, — а разбор фразы промахивается: на голом
    # «Landscape» он молчит.
    #
    # Без этого сервер считал поле незакрытым, модель спрашивала про формат
    # второй раз, а кнопки под её вопросом показывали уже следующее поле. Человек
    # видел вопрос про одно и варианты про другое — и отвечал не туда.
    answers: dict[str, str] = Field(default_factory=dict, max_length=16)


class Option(BaseModel):
    """Готовый ответ на заданный вопрос: подпись человеку, значение нам."""

    label: str
    value: str


class ChatResponse(BaseModel):
    reply: str
    # Просьба описана достаточно — можно браться за кадр, не спрашивая больше.
    ready: bool = False
    # Чем отвечать на вопрос: готовые варианты вместо ввода с нуля.
    #
    # Печатать ответ человек всегда может, но выбрать легче, чем сочинить, — а
    # на вопрос «кто из них вы» печатать вообще нечего.
    options: list[Option] = []
    # Роли приложенных снимков, если мы их разобрали: `person` или `style` по
    # порядку. Приложение отдаёт их в генерацию, чтобы зрение не смотрело на те
    # же картинки второй раз за деньги.
    roles: list[str] = []
    # О чём именно спрошено: `photo`, `format`, `technique`, … или пусто, если
    # спрашивать больше не о чем.
    #
    # Приложению это нужно, чтобы показать под ответом то, чем на него отвечают.
    # Раньше оно показывало выбор стиля всегда — и под вопросом «хотите себя на
    # постере?» стояли кнопки «Cartoon 3D, Cozy scene, Anime». Вопрос и ответ
    # на одном экране не сходились, а человек должен был догадаться, что кнопки
    # не про то.
    ask_about: Optional[str] = None


class StoredMessage(BaseModel):
    id: int
    role: str
    content: Optional[str] = None
    generation_id: Optional[str] = None
    result_url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    # Снимок, приложенный человеком к этой реплике.
    attachment_url: Optional[str] = None
    created_at: datetime


def _serialize(row: m.ChatMessage, result_media_id: Optional[str]) -> StoredMessage:
    return StoredMessage(
        id=row.id,
        role=row.role,
        content=row.content,
        generation_id=row.generation_id,
        result_url=f"/api/media/{result_media_id}" if result_media_id else None,
        thumbnail_url=f"/api/media/{result_media_id}?thumb=true" if result_media_id else None,
        attachment_url=f"/api/media/{row.media_id}" if row.media_id else None,
        created_at=row.created_at,
    )


@router.post("/chat", response_model=ChatResponse)
async def chat(
    body: ChatRequest,
    ctx: Optional[Context] = Depends(optional_context),
    db: AsyncSession = Depends(get_db_session),
) -> ChatResponse:
    """Answer, and store both sides of the turn.

    Works without a session too — a guest gets a session on first launch, so in
    practice this only happens for a probe or a browser without one; nothing is
    stored then.
    """
    if not body.message.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Say something first.")

    if ctx is None:
        # Без сессии мы ничего не разбирали, и «готово» тут означало бы
        # «не считали» — а приложение прочитает его как «запускай».
        reply = await gpt_service.chat_reply(message=body.message, history=[])
        return ChatResponse(reply=reply, ready=False)

    user, _ = ctx
    history = await chat_repo.context_messages(db, user, limit=settings.chat_context_messages)

    # О чём спрашивать — решается здесь, а не моделью. Разбор идёт по всей
    # реплике человека в этом разговоре, а не по последнему сообщению: стиль он
    # назвал первой фразой, и спрашивать о нём на третьей — то же самое, что не
    # слушать вовсе.
    said = " ".join(
        [m["content"] for m in history if m.get("role") == "user" and m.get("content")]
        + [body.message]
    )
    intent = conversation.detect_intent(said)
    # Профиль весит как приложенная фотография: человек в кадре уже обеспечен,
    # и просьба считается описанной на эти тридцать процентов.
    has_profile = await profiles_repo.get_default(db, user.id) is not None
    known = await gpt_service.extract_slots(said, conversation.slots_for(intent))
    # Выбранное кнопкой сильнее разобранного из речи: это не догадка о
    # сказанном, а сам ответ.
    known.update({
        field: value for field, value in body.answers.items()
        if field in gpt_service.SLOT_MEANING and value
    })
    roles, role_options = await _resolve_roles(db, user, body, has_profile)
    if role_options:
        reply = "Quick check — which one is you?"
        await chat_repo.add_message(db, user_id=user.id, role="user",
                                    content=body.message or None, media_id=body.media_id)
        await chat_repo.add_message(db, user_id=user.id, role="assistant", content=reply)
        return ChatResponse(reply=reply, ask_about="who", options=role_options)

    # Образец отвечает за то, как это выглядит: спрашивать про технику и цвета
    # после «сделай в такой же стилистике» — значит не смотреть туда, куда
    # человек показывает пальцем.
    if "style" in roles:
        known = conversation.with_sample(known, intent=intent)

    ready = conversation.is_ready(
        known,
        intent=intent,
        has_photo=body.photo_attached or has_profile,
        asked=body.asked,
    )

    gap = conversation.next_gap(
        known,
        intent=intent,
        photo_attached=body.photo_attached,
        # Профиль, а не «когда-то что-то загружал»: спрашивать про фотографию
        # у того, чьё лицо мы храним, — та же глухота, что и переспрашивать
        # сказанное.
        photo_on_file=has_profile,
        asked=body.asked,
    )
    # Готово — значит не спрашиваем. Раньше «готово» и «вопрос» приходили вместе,
    # и приложение честно останавливалось на вопросе: сказанного хватало на кадр,
    # а человек читал очередное «где будет сцена?». Незакрытое поле есть почти
    # всегда — это не повод не работать.
    if ready:
        gap = None

    reply = await gpt_service.chat_reply(
        message=body.message,
        history=history,
        ask_about=conversation.ASK_ABOUT.get(gap) if gap else None,
        known=known,
        photo_attached=body.photo_attached,
    )

    await chat_repo.add_message(db, user_id=user.id, role="user",
                                content=body.message or None, media_id=body.media_id)
    await chat_repo.add_message(db, user_id=user.id, role="assistant", content=reply)
    return ChatResponse(
        reply=reply,
        ask_about=gap,
        ready=ready,
        roles=roles,
    )


async def _resolve_roles(
    db: AsyncSession, user, body: ChatRequest, has_profile: bool
) -> tuple[list[str], list[Option]]:
    """Кто из приложенных снимков человек, а кто образец.

    Возвращает либо роли, либо варианты для вопроса. Третьего не дано: гадать на
    этом месте нельзя — ошибка здесь оборачивается чужим лицом в кадре, за
    который уже списано.

    Спрашиваем именно здесь, до генерации. Раньше сомнение разрешалось само,
    молча и в пользу самого вероятного; человек узнавал о нашей догадке по
    готовой картинке, и исправить её мог только новой генерацией за новые
    деньги.
    """
    # С профилем разбираем и одиночное вложение: лицо у нас есть, и приложенная
    # картинка чаще образец, чем человек.
    least = 1 if has_profile else 2
    if len(body.attachment_ids) < least:
        return [], []

    storage = get_storage()
    images: list[tuple[bytes, str]] = []
    for media_id in body.attachment_ids:
        asset = await db.get(m.MediaAsset, media_id)
        if asset is None or asset.user_id != user.id or asset.deleted_at is not None:
            return [], []
        data = await storage.get(asset.storage_key)
        if not data:
            return [], []
        images.append((data, asset.mime or "image/jpeg"))

    roles = await gpt_service.reference_roles(images, body.message, person_known=has_profile)
    people = roles.count("person")

    # Однозначно — три случая, и только они.
    if roles:
        # Всё образцы при собранном профиле: субъект есть, он просто не здесь.
        if people == 0 and has_profile:
            return roles, []
        # Без профиля единственный человек среди снимков — он и есть субъект,
        # больше взять его неоткуда.
        if people == 1 and not has_profile:
            return roles, []
        # Один снимок, и на нём человек: «возьми этот вместо сохранённого» —
        # других прочтений нет.
        if people == 1 and len(images) == 1:
            return roles, []

    # Остальное — сомнение, и его нельзя разрешать за человека.
    #
    # Главный случай: профиль есть, а среди приложенного зрение видит людей.
    # «Сделай меня в стилистике вот этого портрета» и «нарисуй нас двоих» —
    # это одинаковые картинки и разные просьбы, и разница живёт не в пикселях,
    # а в голове у того, кто их прислал. Спросить дешевле, чем ошибиться:
    # вопрос стоит ноль, а чужое лицо в кадре — списанный TOONTOON и новую
    # генерацию, чтобы это исправить.

    # Иначе спрашиваем. Подписи — по порядку, как они лежат в переписке:
    # «первая» и «вторая» человек сопоставит с картинками глазами, а имён у
    # снимков нет и быть не может.
    order = ["The first photo", "The second photo", "The third photo",
             "The fourth photo", "The fifth photo"]
    options = [
        Option(label=order[i] if i < len(order) else f"Photo {i + 1}", value=str(i))
        for i in range(len(body.attachment_ids))
    ]
    if has_profile:
        options.append(Option(label="Use my saved photos", value="profile"))
    # Двое в кадре — законная просьба, и она не должна теряться среди «кто из
    # них вы»: без этого варианта человек вынужден выбрать одного из двоих,
    # хотя хотел обоих.
    if len(body.attachment_ids) == 2:
        options.append(Option(label="Both of us", value="both"))
    return [], options


class AttachmentRequest(BaseModel):
    media_id: str


class AttachmentResponse(BaseModel):
    # Что мы сказали про сам снимок. Разговор начинается с ответа на
    # показанное, а не со списка услуг.
    remark: str = ""
    ideas: list[str] = []


@router.post("/chat/attachment", response_model=AttachmentResponse)
async def attachment(
    body: AttachmentRequest,
    ctx: Context = Depends(required_context),
    db: AsyncSession = Depends(get_db_session),
) -> AttachmentResponse:
    """Снимок кладётся в переписку и сразу получает ответ — четыре идеи.

    Одной ручкой, а не двумя, потому что для человека это одно событие: он
    приложил фотографию и смотрит, что мы скажем. Две ручки означали бы две
    задержки подряд, и вторую он проводил бы, глядя на своё фото в пустом треде.

    Реплики за него мы при этом не сочиняем: раньше приложение отправляло
    вместо него «Here is my photo.» — только чтобы пройти проверку на непустой
    текст, — и в переписке стояло то, чего он не писал.
    """
    user, _ = ctx
    asset = await db.get(m.MediaAsset, body.media_id)
    if asset is None or asset.user_id != user.id or asset.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Photo not found")

    await chat_repo.add_message(db, user_id=user.id, role="user", media_id=asset.id)
    data = await get_storage().get(asset.storage_key)
    remark, ideas = await gpt_service.photo_remark_and_ideas(data or b"")
    # Реплика ложится в переписку, идеи — нет. Реплика это разговор, и она
    # должна быть на месте завтра; идеи живут до выбора и после него бессмысленны.
    if remark:
        await chat_repo.add_message(db, user_id=user.id, role="assistant", content=remark)
    return AttachmentResponse(remark=remark, ideas=ideas)


@router.get("/chat/messages", response_model=list[StoredMessage])
async def messages(
    ctx: Context = Depends(required_context),
    db: AsyncSession = Depends(get_db_session),
    limit: int = Query(default=30, ge=1, le=100),
    before: Optional[datetime] = Query(default=None, description="Курсор: created_at предыдущей страницы"),
) -> list[StoredMessage]:
    user, _ = ctx
    rows = await chat_repo.list_messages(db, user, limit=limit, before=before)

    # Results are rendered inline in the thread, so a message that produced one
    # carries its media link.
    media_by_generation: dict[str, Optional[str]] = {}
    generation_ids = [r.generation_id for r in rows if r.generation_id]
    if generation_ids:
        from sqlalchemy import select

        stmt = select(m.Generation.id, m.Generation.result_media_id).where(
            m.Generation.id.in_(generation_ids)
        )
        media_by_generation = {gid: mid for gid, mid in (await db.execute(stmt)).all()}

    return [
        _serialize(row, media_by_generation.get(row.generation_id) if row.generation_id else None)
        for row in rows
    ]


@router.post("/chat/clear")
async def clear(
    ctx: Context = Depends(required_context),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Start a new conversation.

    Экран после этого пуст, а строки остаются в базе: они привязаны к работам и
    к списаниям TOONTOON, и удалять их ради вида нельзя. Раньше очистка сбрасывала
    только память модели, а переписка оставалась на экране — человек видел свой
    разговор и говорил с собеседником, который его забыл.
    """
    user, _ = ctx
    await chat_repo.clear_context(db, user.id)
    return {"ok": True}
