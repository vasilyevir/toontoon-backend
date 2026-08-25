"""Разговор помнит сказанное — по записи, а не по последним двадцати репликам.

Всё, что здесь проверяется, однажды сломалось молча и измеримо. Замер на живом
разговоре: человек начинает с «хочу постер в стиле аниме, бело-сине-красный,
как для NBA», дальше двадцать коротких реплик — и на двадцать втором ходу база
печатает «первая фраза («аниме») ещё в окне: False | что знаем сейчас: {}».
Разговор снова спрашивал про то, что ему уже сказали, а кадр уезжал без
техники, палитры и назначения.

Тесты идут против настоящего PostgreSQL: правило «новое побеждает, молчание
сохраняет» живёт в запросе с ON CONFLICT, и на подделке базы не проверяется.

    PYTHONPATH=. .venv/bin/python -m pytest tests -q
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import delete

from app.db import models as m
from app.db.repositories import state as state_repo
from app.routers.generate import _carried_over, _for_the_frame
from app.db.session import connect, disconnect, get_factory

@pytest_asyncio.fixture
async def db():
    await connect()
    factory = get_factory()
    async with factory() as session:
        user = m.User(kind="guest")
        session.add(user)
        await session.flush()
        await session.commit()

        yield session, user

        await session.execute(
            delete(m.ConversationState).where(m.ConversationState.user_id == user.id)
        )
        await session.execute(delete(m.User).where(m.User.id == user.id))
        await session.commit()
    await disconnect()


@pytest.mark.asyncio
async def test_silence_keeps_what_was_said(db):
    """«Ага» не отменяет аниме.

    Пустой разбор — самый частый ответ: человек говорит про одно поле за раз.
    Раньше это и не требовалось замечать, потому что разбирался весь тред; с
    записью это становится главным правилом слияния.
    """
    session, user = db
    await state_repo.remember(session, user, intent="poster",
                              fresh={"technique": "anime", "palette": "white blue red"})
    known = await state_repo.remember(session, user, fresh={})

    assert known == {"technique": "anime", "palette": "white blue red"}
    intent, known = await state_repo.load(session, user)
    assert intent == "poster"
    assert known["technique"] == "anime"


@pytest.mark.asyncio
async def test_new_value_wins(db):
    """Передумал — значит передумал: последнее слово сильнее первого."""
    session, user = db
    await state_repo.remember(session, user, fresh={"technique": "anime"})
    known = await state_repo.remember(session, user, fresh={"technique": "watercolour"})

    assert known["technique"] == "watercolour"


@pytest.mark.asyncio
async def test_intent_is_only_what_was_named(db):
    """Умолчание не записывается.

    «Не названо» и «портрет» — разные ответы: если писать угаданное, оно потом
    перебьёт сказанное, и «хочу постер» через десять реплик станет портретом.
    """
    session, user = db
    await state_repo.remember(session, user, intent=None, fresh={"place": "roof"})
    intent, _ = await state_repo.load(session, user)
    assert intent is None

    await state_repo.remember(session, user, intent="poster", fresh={})
    intent, _ = await state_repo.load(session, user)
    assert intent == "poster"

    # Молчание про назначение не стирает названное.
    await state_repo.remember(session, user, intent=None, fresh={"light": "sunset"})
    intent, known = await state_repo.load(session, user)
    assert intent == "poster"
    assert known["light"] == "sunset"


@pytest.mark.asyncio
async def test_clear_makes_it_stale(db):
    """«Очистка» обесценивает запись, не удаляя её.

    Человек начинает с чистого экрана — и кадр должен собираться с чистого
    листа тоже. Сама строка остаётся: по ней разбирают жалобы на кадр.
    """
    session, user = db
    await state_repo.remember(session, user, intent="poster", fresh={"technique": "anime"})

    user.chat_context_started_at = datetime.now(timezone.utc) + timedelta(seconds=1)
    await session.flush()

    intent, known = await state_repo.load(session, user)
    assert intent is None
    assert known == {}

    # И следующая реплика начинает набор заново, а не дописывает старый.
    known = await state_repo.remember(session, user, fresh={"place": "beach"})
    assert known == {"place": "beach"}


@pytest.mark.asyncio
async def test_survives_a_window_of_small_talk(db):
    """Тот самый замер: двадцать коротких реплик не выбивают первую фразу.

    Это и есть проверяемое обещание — сказанное перестаёт умирать по расписанию
    окна, потому что живёт не в нём.
    """
    session, user = db
    await state_repo.remember(session, user, intent="poster", fresh={
        "technique": "anime", "palette": "white blue red", "reference": "like an NBA poster",
    })
    for _ in range(20):
        await state_repo.remember(session, user, fresh={})

    intent, known = await state_repo.load(session, user)
    assert intent == "poster"
    assert known["technique"] == "anime"
    assert known["reference"] == "like an NBA poster"


# ─── Кадру достаётся то же, что и разговору ──────────────────────────────────


def test_carries_over_only_what_fell_out_of_the_window():
    """К просьбе дописывается выпавшее, а не всё подряд.

    Кадр собирается из слов человека; повторить в них то, что и так сказано в
    окне, — значит утяжелить сцену дублем. Пропадало же обратное: техника и
    палитра из первой фразы не доезжали до кадра вовсе.
    """
    remembered = {"technique": "anime", "palette": "white, blue, red", "place": "roof"}
    said = "а давай на крыше, roof подойдёт"

    carried = _carried_over(remembered, said)
    assert "anime" in carried and "white, blue, red" in carried
    assert "roof" not in carried


def test_the_sample_note_never_reaches_the_frame():
    """«from the attached sample» — ответ разговору, а не слово для картинки."""
    kept = _for_the_frame({"technique": "from the attached sample", "place": "roof"})
    assert kept == {"place": "roof"}


def test_nothing_lost_means_nothing_added():
    """Когда всё сказанное ещё в окне, приписывать нечего."""
    assert _carried_over({"place": "roof"}, "сделай на крыше, roof") is None
    assert _carried_over({}, "что угодно") is None


@pytest.mark.asyncio
async def test_a_wrong_reading_can_be_taken_back(db):
    """Понятое неверно снимается касанием, а не «Очисткой».

    Правило слияния сохраняет прежнее значение, пока не назвали новое, — и без
    этого одна ошибка разбора («лето» вместо «света») держалась бы до конца
    разговора, попадая в каждый следующий кадр.
    """
    session, user = db
    await state_repo.remember(session, user, intent="poster",
                              fresh={"technique": "anime", "light": "summer"})

    known = await state_repo.drop(session, user, ["light"])
    assert known == {"technique": "anime"}

    intent, known = await state_repo.load(session, user)
    assert intent == "poster"
    assert "light" not in known

    # И назначение тоже: «постер» мог быть услышан в слове «по стеру».
    await state_repo.drop(session, user, ["intent"])
    intent, _ = await state_repo.load(session, user)
    assert intent is None


@pytest.mark.asyncio
async def test_what_was_said_yesterday_stops_counting(db):
    """У сказанного про картинку есть срок годности — сутки.

    Время у полей записывалось с самого начала, но им никто не пользовался:
    «в кофейне», сказанное неделю назад, весило столько же, сколько сказанное
    минуту назад. Человек возвращается назавтра к новой картинке.
    """
    session, user = db
    await state_repo.remember(session, user, intent="poster",
                              fresh={"place": "in a cafe", "technique": "anime"})

    row = await session.get(m.ConversationState, user.id)
    old = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()
    row.slots = {"place": {"value": "in a cafe", "at": old},
                 "technique": {"value": "anime", "at": datetime.now(timezone.utc).isoformat()}}
    await session.flush()

    intent, known = await state_repo.load(session, user)
    assert known == {"technique": "anime"}
    # Назначение по часам не гаснет: оно не описывает картинку, а называет, что
    # человек делает, и меняется своими словами.
    assert intent == "poster"

    # И давнее не переживает следующий ход: иначе оно осталось бы навсегда,
    # переписываясь вместе со свежим.
    known = await state_repo.remember(session, user, fresh={"light": "sunset"})
    assert "place" not in known


@pytest.mark.asyncio
async def test_the_questions_asked_survive_a_restart(db):
    """Список заданных вопросов жил только в приложении.

    Закрыл и открыл — список пуст, и разговор снова спрашивает про фотографию,
    о которой вчера уже спрашивал. Это та самая глухота, ради защиты от которой
    список и заводили: «тебя не слушали» читается по повтору вопроса.
    """
    session, user = db
    assert await state_repo.asked_about(session, user) == []

    await state_repo.remember(session, user, fresh={state_repo.ASKED: "photo,format"})
    assert await state_repo.asked_about(session, user) == ["photo", "format"]

    # Служебный ключ наружу не выходит: строка понятого показывает сказанное
    # человеком, а не наши вопросы к нему.
    _, known = await state_repo.load(session, user)
    assert state_repo.ASKED not in known

    # «Очистка» — новый разговор, новые вопросы.
    user.chat_context_started_at = datetime.now(timezone.utc) + timedelta(seconds=1)
    await session.flush()
    assert await state_repo.asked_about(session, user) == []
