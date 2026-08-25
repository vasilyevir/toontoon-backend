"""Что мы поняли про просьбу — записанное, а не пересказанное.

Разговор раньше помнил ровно двадцать последних реплик и разбирал их заново
каждый ход. Замер показал цену: человек называет технику, палитру и назначение
первой фразой, двадцать коротких «ага» вытесняют её из окна — и разговор снова
спрашивает, хочет ли он себя в кадре. Забылось не потому, что давно, а потому,
что нигде не записано.

Здесь записано. Разбор идёт по новой реплике, а не по всему треду (в литературе
про диалоги это называют UPDATE против STATE), и результат сливается в строку
человека. Два следствия сразу: сказанное перестаёт умирать при прокрутке, а
стоимость хода перестаёт расти с длиной разговора.

Правило слияния одно: новое значение побеждает, молчание сохраняет прежнее.
Молчание — самый частый ответ разбора, и путать его с отказом нельзя: «ага» не
означает, что человек передумал насчёт аниме.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import models as m


# Сколько живёт сказанное про картинку.
#
# Время у полей записывалось с самого начала, но им никто не пользовался: «в
# кофейне», сказанное неделю назад, весило столько же, сколько сказанное минуту
# назад. Человек возвращается назавтра к новой картинке, а не ко вчерашней, и
# молча дописывать в неё вчерашнее место — то же самое, что не слушать.
#
# Сутки, а не час: разговор о картинке легко растягивается на вечер, и терять
# сказанное за ужином было бы хуже, чем помнить лишнее.
#
# Назначение сюда не входит. Оно не описывает картинку, а называет, что человек
# вообще делает, и меняется своими словами — «нет, лучше открытку», — а не по
# часам.
SLOT_LIFETIME = timedelta(hours=24)


def _fresh(slots: dict, *, now: datetime | None = None) -> dict:
    """Поля, сказанные не слишком давно."""
    now = now or datetime.now(timezone.utc)
    alive = {}
    for field, cell in (slots or {}).items():
        if not isinstance(cell, dict) or not cell.get("value"):
            continue
        try:
            said_at = datetime.fromisoformat(str(cell.get("at")))
        except (TypeError, ValueError):
            # Без времени судить о давности нечем — считаем свежим: потерять
            # сказанное хуже, чем додержать лишнее.
            alive[field] = cell
            continue
        if said_at.tzinfo is None:
            said_at = said_at.replace(tzinfo=timezone.utc)
        if now - said_at <= SLOT_LIFETIME:
            alive[field] = cell
    return alive


def _values(slots: dict) -> dict[str, str]:
    """Голые значения из хранимого вида — наружу время полей не нужно."""
    return {
        field: cell["value"]
        for field, cell in (slots or {}).items()
        if isinstance(cell, dict) and cell.get("value")
    }


# О чём уже спрашивали. Ключ служебный: он лежит среди слотов, но слотом не
# является — это не то, что человек сказал, а то, что мы у него спросили.
#
# Живёт здесь, а не в приложении, потому что всё остальное уже здесь. Пока
# список был только на клиенте, перезапуск приложения его стирал, и разговор
# снова спрашивал про фотографию, о которой вчера уже спрашивал, — та самая
# глухота, ради защиты от которой список и заводили.
ASKED = "_asked"


async def load(session: AsyncSession, user: m.User) -> tuple[str | None, dict[str, str]]:
    """Назначение и поля, которые человек уже назвал.

    «Очистка» не удаляет строку, а обесценивает её: если состояние записано до
    последней очистки, оно больше не считается сказанным. Так же ведёт себя и
    переписка — иначе человек начал бы с чистого экрана и получил кадр по
    позапрошлой просьбе.
    """
    row = await session.get(m.ConversationState, user.id)
    if row is None:
        return None, {}
    if user.chat_context_started_at is not None and (
        row.started_at is None or row.started_at < user.chat_context_started_at
    ):
        return None, {}
    known = _values(_fresh(row.slots))
    known.pop(ASKED, None)
    return row.intent, known


async def remember(
    session: AsyncSession,
    user: m.User,
    *,
    intent: str | None = None,
    fresh: dict[str, str] | None = None,
    replace: bool = False,
) -> dict[str, str]:
    """Слить сказанное только что с тем, что человек говорил раньше.

    Возвращает всё известное о просьбе — то, чем разговор пользуется дальше.

    ``replace`` — просьба началась заново: человек назвал другую картину, и
    прежние поля описывают уже не её. Молчание сохраняет сказанное, но отказ —
    не молчание, и без этого признака новогодняя открытка уезжала с палитрой от
    баскетбольного постера.
    """
    row = await session.get(m.ConversationState, user.id)
    stale = row is not None and user.chat_context_started_at is not None and (
        row.started_at is None or row.started_at < user.chat_context_started_at
    )
    # Давнее не только не отдаётся наружу, но и не переживает следующий ход:
    # иначе оно оставалось бы навсегда, переписываясь вместе со свежим.
    slots: dict = {} if (row is None or stale or replace) else _fresh(row.slots)
    if stale:
        intent = intent or None

    now = datetime.now(timezone.utc).isoformat()
    for field, value in (fresh or {}).items():
        if not value:
            # Пустое значение — это молчание разбора, а не «человек передумал».
            continue
        slots[field] = {"value": value, "at": now}

    kept_intent = intent or (None if (row is None or stale or replace) else row.intent)
    await session.execute(
        insert(m.ConversationState)
        .values(
            user_id=user.id, intent=kept_intent, slots=slots,
            started_at=user.chat_context_started_at,
        )
        .on_conflict_do_update(
            index_elements=[m.ConversationState.user_id],
            set_={
                "intent": kept_intent,
                "slots": slots,
                "started_at": user.chat_context_started_at,
                "updated_at": datetime.now(timezone.utc),
            },
        )
    )
    return _values(slots)


async def asked_about(session: AsyncSession, user: m.User) -> list[str]:
    """О чём в этом разговоре уже спрашивали.

    Срок годности тот же, что у полей: вопрос суточной давности человек не
    помнит, и повторить его не обидно — обидно повторять сказанное час назад.
    """
    row = await session.get(m.ConversationState, user.id)
    if row is None:
        return []
    if user.chat_context_started_at is not None and (
        row.started_at is None or row.started_at < user.chat_context_started_at
    ):
        return []
    cell = _fresh(row.slots).get(ASKED)
    return [f for f in str((cell or {}).get("value", "")).split(",") if f]


async def drop(session: AsyncSession, user: m.User, fields: list[str]) -> dict[str, str]:
    """Убрать поле из записи — человек говорит, что мы поняли неверно.

    Единственный способ снять понятое: правило слияния сохраняет молчание, и
    без этого ошибка разбора держалась бы до самой «Очистки». Поэтому строка
    под перепиской не только показывает — по касанию она и стирает.
    """
    row = await session.get(m.ConversationState, user.id)
    if row is None:
        return {}
    slots = {f: cell for f, cell in (row.slots or {}).items() if f not in fields}
    row.slots = slots
    if "intent" in fields:
        row.intent = None
    await session.flush()
    return _values(slots)


async def forget(session: AsyncSession, user_id: str) -> None:
    """Убрать состояние совсем — для удаления учётной записи."""
    row = await session.get(m.ConversationState, user_id)
    if row is not None:
        await session.delete(row)
