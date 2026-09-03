"""Chat repository — one continuous thread per user (CH-20).

There is no ``chats`` table and no thread list: finding an old work is what the
history tab is for, and keeping two mechanisms for the same job means keeping
two answers to the same question.

"Clear" does not delete anything. It moves a marker on the user, so the model
forgets the previous conversation while the person can still scroll back to it.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional, Sequence

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import models as m


async def record_attachments(session: AsyncSession, *, user_id: str, media_ids: list[str]) -> int:
    """Положить приложенные снимки в переписку — каждый один раз.

    Раньше это делала отдельная ручка: снимок отправлялся сам, в тот же миг,
    как его выбрали, и она же заводила ему реплику. Теперь фотография ждёт в
    поле ввода и уезжает вместе с сообщением — а `/api/chat` про неё не знал
    ничего, и снимок в переписке не сохранялся вовсе. На экране он был, до
    первого перезапуска: тред перечитывается с сервера, а там его нет.

    Идемпотентно, и это обязательно: приложение шлёт список приложенного за
    весь разговор, а не за последнее сообщение. Без проверки каждый следующий
    ответ добавлял бы к треду ещё по копии всех прежних снимков.
    """
    if not media_ids:
        return 0
    # Только свои снимки. Байты чужого `med_…` через `/api/media` не отдаются,
    # но и записывать чужой идентификатор в свою переписку незачем.
    свои = set((await session.scalars(
        select(m.MediaAsset.id).where(
            m.MediaAsset.user_id == user_id,
            m.MediaAsset.id.in_(media_ids),
            m.MediaAsset.deleted_at.is_(None),
        )
    )).all())
    media_ids = [i for i in media_ids if i in свои]
    if not media_ids:
        return 0
    seen = set((await session.scalars(
        select(m.ChatMessage.media_id).where(
            m.ChatMessage.user_id == user_id,
            m.ChatMessage.media_id.in_(media_ids),
        )
    )).all())
    added = 0
    for media_id in media_ids:
        if media_id in seen:
            continue
        await add_message(session, user_id=user_id, role="user", media_id=media_id)
        added += 1
    return added


async def add_message(
    session: AsyncSession,
    *,
    user_id: str,
    role: str,
    content: Optional[str] = None,
    generation_id: Optional[str] = None,
    media_id: Optional[str] = None,
) -> m.ChatMessage:
    message = m.ChatMessage(
        user_id=user_id, role=role, content=content,
        generation_id=generation_id, media_id=media_id,
    )
    session.add(message)
    await session.flush()
    return message


async def list_messages(
    session: AsyncSession,
    user: m.User,
    *,
    limit: int = 30,
    before: Optional[datetime] = None,
) -> Sequence[m.ChatMessage]:
    """A page of the thread, newest first.

    The single thread grows without bound, so loading it whole is not an
    option — that is the price of dropping thread lists, and it is paid here.

    «Очистка» здесь настоящая: всё, что было до неё, не показывается. Раньше
    экран продолжал показывать старое, а модель его уже не помнила — человек
    видел свою переписку и разговаривал с собеседником, который её забыл. Сами
    строки остаются в базе: они привязаны к работам и к списаниям, и удалять их
    ради вида нельзя.
    """
    stmt = (
        select(m.ChatMessage)
        .where(m.ChatMessage.user_id == user.id)
        .order_by(m.ChatMessage.created_at.desc(), m.ChatMessage.id.desc())
        .limit(limit)
    )
    if user.chat_context_started_at is not None:
        stmt = stmt.where(m.ChatMessage.created_at >= user.chat_context_started_at)
    if before is not None:
        stmt = stmt.where(m.ChatMessage.created_at < before)
    return (await session.scalars(stmt)).all()


async def context_messages(
    session: AsyncSession, user: m.User, *, limit: int,
    since: Optional[datetime] = None,
) -> list[dict]:
    """What the model gets: the last N messages since the most recent Clear.

    Capped on purpose — the cost of a request must not grow with the age of the
    conversation, or an active person's chat becomes more expensive every day.

    Реплика, где человек приложил снимок и ничего не написал, раньше выпадала
    отсюда целиком: строки без текста отфильтровывались. Для отвечающей модели
    приложения не было вовсе — а человек его видит в переписке и продолжает
    разговор про него. Картинку она всё равно не увидит, но знать, что снимок
    был и когда, обязана: без этого «сделай в такой же стилистике» повисает в
    воздухе.

    Двадцать при этом берётся ПОСЛЕ отбрасывания пустых строк, а не до. Раньше
    было наоборот, и разговор с вложениями получал окно короче двадцати — тем
    короче, чем больше человек прикладывал.

    ``since`` — не показывать сказанное раньше этого момента. Нужно кадру, а не
    разговору: когда человек говорит «нет, лучше открытку», прежняя просьба
    отменена вся целиком, а не только её назначение. Разговор при этом помнит
    её как контекст беседы — он же на неё отвечал.
    """
    stmt = (
        select(m.ChatMessage)
        .where(m.ChatMessage.user_id == user.id,
               (m.ChatMessage.content.isnot(None)) | (m.ChatMessage.media_id.isnot(None)))
        .order_by(m.ChatMessage.created_at.desc(), m.ChatMessage.id.desc())
        .limit(limit)
    )
    if user.chat_context_started_at is not None:
        stmt = stmt.where(m.ChatMessage.created_at >= user.chat_context_started_at)
    if since is not None:
        stmt = stmt.where(m.ChatMessage.created_at >= since)
    rows = list(await session.scalars(stmt))
    rows.reverse()
    out = []
    for r in rows:
        content = (r.content or "").strip()
        if r.media_id and not content:
            # Пометка, а не выдуманная реплика: человек этих слов не писал, и
            # ставить их от его имени нельзя. Модели достаточно знать, что тут
            # было вложение.
            content = "(attached an image)"
        elif r.media_id:
            content = f"{content} (with an attached image)"
        if content:
            out.append({"role": r.role, "content": content})
    return out


async def clear_context(session: AsyncSession, user_id: str) -> None:
    """Start a new conversation without losing the old one.

    Takes an id, not the user object: the row that ``deps`` hands routers was
    loaded in a different session and is detached, so assigning to it writes
    nothing. This bug is silent — Clear would appear to work while the model
    kept remembering — so the update is issued explicitly.
    """
    await session.execute(
        update(m.User).where(m.User.id == user_id).values(chat_context_started_at=func.now())
    )
    await session.flush()
