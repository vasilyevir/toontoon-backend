"""Правила одного хода разговора: когда браться за кадр, что показывать, что забывать.

Всё здесь найдено живым прогоном, а не чтением кода: пять сценариев на русском
через настоящий API, с записью того, что чат спросил и что понял. Каждый тест
подписан тем, что было в записи.

    PYTHONPATH=. .venv/bin/python -m pytest tests -q
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import delete

from app.db import models as m
from app.db.repositories import state as state_repo
from app.db.session import connect, disconnect, get_factory
from app.routers.chat import _worth_showing
from app.services import conversation


# ─── Пол под готовностью ─────────────────────────────────────────────────────


def test_nothing_known_and_no_photo_is_not_ready():
    """«Сделай меня супергероем» → спросили про снимок → «не знаю, на твой вкус».

    В записи стояло `готово: True | понято: {}` — портрет без человека и без
    единого поля считался описанным. Кадр тогда придумывается целиком, а
    списание за него настоящее.
    """
    assert not conversation.is_ready({}, intent="portrait", has_photo=False,
                                     asked=["photo"])


def test_one_question_is_still_the_ceiling():
    """Пол не отменяет потолка: сказано хоть что-то — больше не допрашиваем.

    Человек пришёл за картинкой, а не за анкетой. Поправить готовое дешевле,
    чем вообразить несуществующее.
    """
    assert conversation.is_ready({"technique": "anime"}, intent="portrait",
                                 has_photo=False, asked=["photo"])
    # Снимка достаточно и без слов: лицо — самое весомое, что можно сказать.
    assert conversation.is_ready({}, intent="portrait", has_photo=True, asked=["photo"])


# ─── Вопрос про нас — не заказ картинки ──────────────────────────────────────


@pytest.mark.parametrize("text, about_us", [
    ("Что ты умеешь?", True),
    ("Сколько это стоит?", True),
    ("Можешь сделать видео?", True),
    ("How much does it cost?", True),
    # Заказ, просто вежливый: вопросительный знак сам по себе ничего не решает.
    ("Сделай меня супергероем", False),
    ("16:9", False),
])
def test_a_question_about_us_is_not_an_order(text, about_us):
    """Под ответом про цены стояла панель загрузки фотографии.

    Разговор читал любую реплику как просьбу нарисовать: назначение по
    умолчанию — портрет, а первый пробел в портрете — снимок.
    """
    assert conversation.is_a_question_about_us(text, said_anything=False) is about_us


def test_words_about_the_picture_beat_the_question_mark():
    """«А можно на крыше?» — это заказ: из него вынулось место."""
    assert not conversation.is_a_question_about_us("а можно на крыше?", said_anything=True)


# ─── Что показывать человеку ─────────────────────────────────────────────────


def test_internal_notes_never_reach_the_screen():
    """На экране стоял чип «from the attached sample».

    Пометка про образец — ответ разговору, а не слово человека. Сам образец
    виден в треде картинкой, и подписывать его английской служебной строкой
    посреди русской переписки незачем.
    """
    shown = _worth_showing({"technique": "from the attached sample", "place": "on a roof"})
    assert shown == {"place": "on a roof"}


# ─── Новая просьба заменяет прежнюю ──────────────────────────────────────────


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
            delete(m.ConversationState).where(m.ConversationState.user_id == user.id))
        await session.execute(delete(m.User).where(m.User.id == user.id))
        await session.commit()
    await disconnect()


@pytest.mark.asyncio
async def test_a_new_picture_starts_from_scratch(db):
    """«Хочу постер про баскетбол, красно-синий» → «нет, лучше открытку на новый год».

    В записи новогодняя открытка уезжала с палитрой и местом от баскетбольного
    постера: правило «молчание сохраняет прежнее» не отличает молчания от
    отказа. Названное другое назначение — это отказ.
    """
    session, user = db
    await state_repo.remember(session, user, intent="poster",
                              fresh={"palette": "red and blue", "place": "basketball"})
    known = await state_repo.remember(session, user, intent="card",
                                      fresh={"occasion": "New Year"}, replace=True)
    assert known == {"occasion": "New Year"}

    intent, known = await state_repo.load(session, user)
    assert intent == "card"
    assert "palette" not in known


# ─── Язык и обещание про лицо ────────────────────────────────────────────────


def test_the_reply_speaks_the_language_of_the_person():
    """Написанное руками не должно отвечать на другом языке, чем модель.

    Реплик таких немного — приветствие без ключа, извинение за отказ сети,
    «кто из них вы», — но стоят они в той же переписке, и английская строка
    посреди русского разговора читается как чужая.
    """
    from app.services import gpt

    assert gpt.said_in("Сделай меня супергероем", ru="да", en="yes") == "да"
    assert gpt.said_in("Make me a superhero", ru="да", en="yes") == "yes"
    # Ни одной буквы — берём английский: гадать по «16:9» не по чему.
    assert gpt.said_in("16:9", ru="да", en="yes") == "yes"


@pytest.mark.parametrize("text, about_self", [
    ("Сделай меня супергероем", True),
    ("Постер: я в форме Lakers", True),
    ("моё фото приложил", True),
    ("Make me a superhero", True),
    ("Нарисуй рыжего кота", False),
    ("постер про баскетбол", False),
])
def test_asking_for_yourself_is_read_from_the_words(text, about_self):
    """Просит ли человек себя — решают его слова, а не назначение по умолчанию.

    Без этого предупреждение «без фото лицо будет не ваше» уехало бы и на
    рыжего кота: назначение по умолчанию — портрет, и снимок в нём обязателен.
    """
    from app.services import gpt

    assert gpt.asks_for_self(text) is about_self


def test_the_missing_face_is_said_out_loud():
    """Молча нарисовать постороннего и списать за это — худший из ответов.

    Кнопку не отнимаем: человек может пойти дальше и без снимка. Но узнать об
    этом он должен до кадра, а не по нему.
    """
    from app.services import gpt

    warned = gpt.chat_directive(None, {"place": "on a roof"}, no_face_on_file=True)
    assert "no photo of them is attached" in warned
    assert "Do not refuse" in warned

    quiet = gpt.chat_directive(None, {"place": "on a roof"})
    assert "no photo of them" not in quiet


# ─── Образец решает форму кадра ──────────────────────────────────────────────


def test_the_frame_takes_its_shape_from_the_sample():
    """Горизонтальный постер приезжал вертикальным `9:16` по умолчанию.

    Композицию у образца просили с самого начала, а форму брали из умолчания —
    и вертикальная обрезка ломала ровно ту плакатную вёрстку, за которой
    человек пришёл.
    """
    from PIL import Image
    import io

    from app.storage import images

    def shot(width: int, height: int) -> bytes:
        buf = io.BytesIO()
        Image.new("RGB", (width, height), "white").save(buf, format="JPEG")
        return buf.getvalue()

    assert images.aspect_of(shot(2688, 1520)) == "16:9"
    assert images.aspect_of(shot(768, 1376)) == "9:16"
    assert images.aspect_of(shot(1000, 1000)) == "1:1"
    # Нечитаемый файл — не повод гадать: остаётся умолчание.
    assert images.aspect_of(b"not an image") is None
