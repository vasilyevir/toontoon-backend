"""Generation repository.

Replaces the Redis list that was capped at 200 entries — history is kept for
good now (CH-13), which is only possible with pagination and an index instead
of "load everything the user ever made".
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import new_id
from app.db import models as m


async def create(
    session: AsyncSession,
    *,
    user_id: str,
    operation: str,
    status: str = "queued",
    style_id: Optional[str] = None,
    prompt: Optional[str] = None,
    request_params: Optional[dict] = None,
    source_media_id: Optional[str] = None,
    cost: int = 0,
    idempotency_key: Optional[str] = None,
) -> m.Generation:
    generation = m.Generation(
        user_id=user_id,
        operation=operation,
        status=status,
        style_id=style_id,
        prompt=prompt,
        # Without the request there is nothing to repeat from history: the
        # result alone cannot be turned back into the question that produced it.
        request_params=request_params or {},
        source_media_id=source_media_id,
        cost=cost,
        idempotency_key=idempotency_key,
    )
    session.add(generation)
    await session.flush()
    return generation


async def get(
    session: AsyncSession, generation_id: str, *, user_id: Optional[str] = None
) -> Optional[m.Generation]:
    generation = await session.get(m.Generation, generation_id)
    if generation is None or generation.deleted_at is not None:
        return None
    if user_id is not None and generation.user_id != user_id:
        return None
    return generation


async def list_for_user(
    session: AsyncSession,
    user_id: str,
    *,
    limit: int = 30,
    before: Optional[datetime] = None,
    operation: Optional[str] = None,
) -> Sequence[m.Generation]:
    """Newest first, one page at a time.

    Paged by creation time rather than by offset: the list grows at the head,
    and an offset would quietly skip or repeat items as it does.
    """
    stmt = (
        select(m.Generation)
        .where(m.Generation.user_id == user_id, m.Generation.deleted_at.is_(None))
        .order_by(m.Generation.created_at.desc())
        .limit(limit)
    )
    if before is not None:
        stmt = stmt.where(m.Generation.created_at < before)
    if operation is not None:
        stmt = stmt.where(m.Generation.operation == operation)
    return (await session.scalars(stmt)).all()


# `clock_timestamp()`, а не `now()`, и это не придирка.
#
# В PostgreSQL `now()` — время НАЧАЛА транзакции, и оно не движется внутри неё.
# Фоновая задача открывает транзакцию, потом минуту рисует, потом ставит
# `finished_at` — и записывала момент, который наступил ДО рисования. Поле
# существовало, заполнялось и показывало ноль секунд на работу, которая шла
# сорок. Ошибка тихая: значение есть, оно правдоподобное, и неправильное.
#
# `clock_timestamp()` возвращает время в момент вызова.


async def mark_done(
    session: AsyncSession, generation: m.Generation, *, result_media_id: str, prompt: Optional[str] = None
) -> m.Generation:
    generation.status = "done"
    generation.result_media_id = result_media_id
    generation.finished_at = func.clock_timestamp()
    if prompt is not None:
        generation.prompt = prompt
    await session.flush()
    return generation


async def pending_for_user(
    session: AsyncSession, user_id: str, *, younger_than: timedelta, limit: int = 5
) -> list[m.Generation]:
    """Работы этого человека, которые сейчас в пути.

    Нужно на холодном старте. Опрос готовности живёт в задаче на экране, а её
    убивает закрытие приложения — идентификатор заказа при этом нигде не
    сохранён. Сервер кадр дорисует и положит в переписку сам, но пока он
    рисуется, вернувшийся человек не видит ничего: ни кадра, ни признака
    работы. Для него заказ пропал вместе с деньгами.

    Старше порога сюда не попадает, и это не оптимизация. Такая работа не
    «идёт», а мертва: её процесс убили, и дорисовывать некому. Показать её как
    идущую значит обещать кадр, которого не будет, — а сверка тем временем
    вернёт за неё деньги. Пусть лучше человек не увидит ничего, чем увидит
    вечное «рисуется».
    """
    db_now = await session.scalar(select(func.now()))
    floor = (db_now or datetime.now(timezone.utc)) - younger_than
    rows = await session.scalars(
        select(m.Generation)
        .where(
            m.Generation.user_id == user_id,
            m.Generation.status.in_(("queued", "running")),
            m.Generation.created_at >= floor,
        )
        .order_by(m.Generation.created_at.desc())
        .limit(limit)
    )
    return list(rows)


async def stale_running(
    session: AsyncSession, *, older_than: timedelta, limit: int = 500
) -> list[m.Generation]:
    """Найти оборвавшиеся, ничего не меняя.

    Отдельно от `fail_stale` не ради красоты: показ в scripts/settle_refunds.py
    обязан перечислить ровно то, за что заплатит выплата. Пока поиск был
    заперт внутри пометки, показ звал запрос по `failed` и оборвавшихся не
    видел — то есть занижал долг и молчал именно о том, что выплата тронет.

    Время берётся у базы, а не у процесса: реплик несколько, часы у них
    расходятся, а решение о чужих деньгах не должно зависеть от того, на какой
    машине выполнилась сверка.
    """
    db_now = await session.scalar(select(func.now()))
    cutoff = (db_now or datetime.now(timezone.utc)) - older_than
    rows = await session.scalars(
        select(m.Generation)
        .where(m.Generation.status == "running", m.Generation.created_at < cutoff)
        .order_by(m.Generation.created_at)
        .limit(limit)
    )
    return list(rows)


async def fail_stale(session: AsyncSession, *, older_than: timedelta) -> list[m.Generation]:
    """Пометить неудачей работы, которые уже никто не дорисует.

    Задача помечает свою работу сама — и удавшуюся, и провалившуюся. Но между
    «начал» и «пометил» её могут убить: выкатка, OOM, перезапуск пода. Тогда
    запись остаётся в `running` навсегда, деньги списаны, и человек видит
    вечное «рисуется». Ни сверка возвратов, ни он сам о ней уже не узнают.

    Порог берётся с запасом. Честная работа — это до трёх обращений к модели по
    три минуты каждое, то есть девять минут в пределе. Всё, что висит дольше
    получаса, мертво наверняка; ошибиться здесь значит отменить чужую работу
    на середине и вернуть деньги за кадр, который вот-вот придёт.

    Время берётся у базы, а не у процесса: реплик несколько, и часы у них
    расходятся — а решение о чужих деньгах не должно зависеть от того, на
    какой машине выполнилась сверка.
    """
    stale = await stale_running(session, older_than=older_than)
    for generation in stale:
        await mark_failed(
            session, generation,
            error="Работа оборвалась: процесс не дожил до конца (найдена сверкой).",
        )
    return stale


async def mark_failed(
    session: AsyncSession, generation: m.Generation, *, error: str
) -> m.Generation:
    """A failed attempt stays in the table.

    The alternative — leaving no trace — makes "I was charged and got nothing"
    impossible to investigate, even when the charge was correctly rolled back.
    """
    generation.status = "failed"
    generation.error = error[:2000]
    generation.finished_at = func.clock_timestamp()
    await session.flush()
    return generation


async def soft_delete(session: AsyncSession, generation: m.Generation) -> None:
    """Hide the work and kill its share link.

    The row survives because the ledger points at it; erasing the bytes is the
    caller's job (media repository), which is also what makes "deleted" true.
    """
    generation.deleted_at = func.now()
    generation.share_id = None
    await session.flush()


async def ensure_share_id(session: AsyncSession, generation: m.Generation) -> str:
    if not generation.share_id:
        generation.share_id = new_id("shr_")
        await session.flush()
    return generation.share_id


async def get_by_share_id(session: AsyncSession, share_id: str) -> Optional[m.Generation]:
    stmt = select(m.Generation).where(
        m.Generation.share_id == share_id, m.Generation.deleted_at.is_(None)
    )
    return await session.scalar(stmt)


async def get_by_idempotency_key(
    session: AsyncSession, *, user_id: str, key: str
) -> Optional[m.Generation]:
    """Заказ, который этот человек уже делал под этим ключом.

    Удалённые тоже считаются: человек мог убрать кадр из истории, и заводить
    вместо повтора новый заказ значило бы списать за него второй раз. Ключ
    отвечает на вопрос «это уже заказывали», а не «это ещё показывается».
    """
    stmt = select(m.Generation).where(
        m.Generation.user_id == user_id,
        m.Generation.idempotency_key == key,
    )
    return await session.scalar(stmt)
