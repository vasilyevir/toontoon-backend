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

from sqlalchemy import func, select
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


def _values(slots: dict, *, service: bool = False) -> dict[str, str]:
    """Голые значения из хранимого вида — наружу время полей не нужно.

    ``service`` — отдать и служебные ключи (`_asked`, `_restarted`). По
    умолчанию их нет: это наши пометки, а не слова человека, и на экране они
    становятся чипом с английской строкой посреди русской переписки.
    """
    return {
        field: cell["value"]
        for field, cell in (slots or {}).items()
        if isinstance(cell, dict) and cell.get("value")
        and (service or not field.startswith("_"))
    }


# О чём уже спрашивали. Ключ служебный: он лежит среди слотов, но слотом не
# является — это не то, что человек сказал, а то, что мы у него спросили.
#
# Живёт здесь, а не в приложении, потому что всё остальное уже здесь. Пока
# список был только на клиенте, перезапуск приложения его стирал, и разговор
# снова спрашивал про фотографию, о которой вчера уже спрашивал, — та самая
# глухота, ради защиты от которой список и заводили.
ASKED = "_asked"

# Когда просьба началась заново. Служебный ключ, как и `_asked`.
#
# Замена состояния чистит поля, но не переписку: в окно, из которого кадр
# берёт слова, «Хочу постер про баскетбол» как лежало, так и лежит. Человек
# сказал «нет, лучше открытку» — и получил новогоднюю открытку с баскетбольным
# мячом. Строка понятого при этом была права, и это худший вид ошибки: она
# показывает, что мы поняли верно, а кадр говорит обратное.
RESTARTED = "_restarted"

# Кто на приложенных снимках: человек или образец стиля. Служебный ключ.
#
# Решение это принимает человек, отвечая на «кто из них вы», — и жило оно
# только в памяти приложения. Закрыл и открыл: в переписке висит вопрос, ответа
# нет, вариантов нет, а роли неизвестны. Разговор упирался в тупик, из которого
# выход был один — начать заново.
#
# Хранится строкой «media_id:роль», через запятую: по тем же правилам, что и
# остальные поля, и гаснет вместе с ними через сутки. Отдельной таблицы это не
# заслуживает — роли живут ровно столько же, сколько сама просьба.
ROLES = "_roles"


def roles_to_slot(roles: dict[str, str]) -> str:
    return ",".join(f"{mid}:{role}" for mid, role in sorted(roles.items()))


def roles_from_slot(raw: str | None) -> dict[str, str]:
    if not raw:
        return {}
    out: dict[str, str] = {}
    for pair in raw.split(","):
        mid, _, role = pair.partition(":")
        if mid and role:
            out[mid] = role
    return out


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
    return row.intent, _values(_fresh(row.slots))


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
    if replace:
        # Отметка нужна кадру, а не разговору: по ней он обрежет окно и не
        # возьмёт слов, от которых человек отказался.
        #
        # Время берётся у той же базы, что раздаёт его репликам, а не у питона.
        # Реплика получает `now()` — то есть время НАЧАЛА транзакции, — и
        # питоновское «сейчас», взятое посреди хода, оказывается позже неё.
        # Обрезка тогда съедала и саму новую просьбу: в кадр не уходило ни
        # слова.
        at_db = await session.scalar(select(func.now()))
        slots[RESTARTED] = {"value": (at_db or datetime.now(timezone.utc)).isoformat(),
                            "at": now}
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


async def roles_of(session: AsyncSession, user: m.User) -> dict[str, str]:
    """Кто на приложенных снимках: человек или образец.

    Читается так же, как `asked_about`, и не через `load`: тот отдаёт только
    сказанное человеком и служебные ключи отсекает — иначе они посыпались бы в
    строку понятого над полем ввода, где им не место.

    Срок годности тот же, что у полей: снимок суточной давности к нынешней
    просьбе отношения уже не имеет.
    """
    row = await session.get(m.ConversationState, user.id)
    if row is None:
        return {}
    if user.chat_context_started_at is not None and (
        row.started_at is None or row.started_at < user.chat_context_started_at
    ):
        return {}
    cell = _fresh(row.slots).get(ROLES)
    return roles_from_slot(str((cell or {}).get("value", "")) or None)


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


async def restarted_at(session: AsyncSession, user: m.User) -> datetime | None:
    """Когда человек отказался от прежней просьбы и начал новую.

    `None` — не отказывался. Тогда кадру достаётся всё окно, как и раньше.
    """
    row = await session.get(m.ConversationState, user.id)
    if row is None:
        return None
    if user.chat_context_started_at is not None and (
        row.started_at is None or row.started_at < user.chat_context_started_at
    ):
        return None
    cell = _fresh(row.slots).get(RESTARTED)
    try:
        return datetime.fromisoformat(str((cell or {}).get("value")))
    except (TypeError, ValueError):
        return None


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
