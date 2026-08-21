"""Слова решают: правка это или новая просьба, нужны ли буквы, чем нарисовано.

Всё, что здесь проверяется, однажды сломалось молча. Человек прикладывал
аниме-постер и просил «сделай со мной в такой же стилистике, а вместо akai
напиши моё имя» — и получал свою фотографию на закатной улице: без стиля, без
букв, зато с приветливой улыбкой из референса. Ни одной ошибки в логах при этом
не было: с точки зрения кода всё отработало.

    PYTHONPATH=. .venv/bin/python -m pytest tests -q
"""
from __future__ import annotations

import pytest

from app.routers.generate import _restates_the_picture, _wants_lettering
from app.services import conversation, gpt, prompt_style


# ─── Правка кадра или новая просьба ──────────────────────────────────────────


@pytest.mark.parametrize("text, named", [
    ("I want to see poster 16:9 in this styles", {"intent": "poster", "format": "16:9"}),
    ("хочу открытку квадратом", {"intent": "card", "format": "1:1"}),
    ("а давай теперь в аниме", {"technique": "anime"}),
    ("сделай горизонтально", {"format": "16:9"}),
])
def test_words_that_name_another_picture(text, named):
    assert _restates_the_picture(text) == named


@pytest.mark.parametrize("text", [
    "сделай фон ночным",
    "убери прямоугольник слева",
    "теплее свет",
    "add a hat",
])
def test_words_that_only_fix_this_one(text):
    # Правка не должна превращаться в новую просьбу: человек доволен кадром и
    # меняет в нём одно, а пересборка с нуля выбросит всё остальное.
    assert _restates_the_picture(text) == {}


def test_plain_phrase_names_no_intent():
    # `detect_intent` всегда отвечает «портрет» — это умолчание, а не ответ.
    # Умолчание не должно перебивать назначение, выбранное минуту назад.
    assert conversation.explicit_intent("сделай фон ночным") is None
    assert conversation.detect_intent("сделай фон ночным") == "portrait"
    assert conversation.explicit_intent("хочу постер") == "poster"


# ─── Буквы ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("text", [
    "а вместо akai напиши мое имя Ilya",
    "нужна надпись сверху",
    "write my name on it",
    "добавь заголовок",
])
def test_asking_for_letters(text):
    assert _wants_lettering(text)


@pytest.mark.parametrize("text", [
    "сделай меня на фоне гор",
    "теплее свет и ближе кадр",
])
def test_not_asking_for_letters(text):
    assert not _wants_lettering(text)


def test_poster_without_words_invents_nothing():
    # Постер без сказанных слов однажды набрал плакатным шрифтом саму просьбу
    # человека: «I WANT TO SEE POSTER 16:9 IN THIS STYLES».
    system = gpt._system_for(editing=True, lettering=False, poster=True)
    assert "do NOT invent any lettering" in system
    assert "MUST appear in the image" not in system


def test_poster_with_words_demands_them():
    system = gpt._system_for(editing=True, lettering=True, poster=True)
    assert "MUST appear in the image" in system


def test_letters_without_a_poster_do_not_bring_poster_layout():
    # «Напиши здесь моё имя» — заказ надписи, а не плаката: композицию человек
    # уже показал образцом.
    system = gpt._system_for(editing=True, lettering=True, poster=False)
    assert "MUST appear in the image" in system
    assert "flat graphic shapes" not in system


# ─── Чем это нарисовано ──────────────────────────────────────────────────────


def test_redraw_names_the_medium_first():
    clause = prompt_style.identity_clause(subject="person", drawn=True,
                                          medium=prompt_style.medium_of("anime"))
    # «Redrawn as a character in this style» начиналось ссылкой на стиль,
    # который в промпте ещё не назван: якорь стоит ниже. Модель к этому моменту
    # уже решила, что правит фотографию.
    assert clause.startswith("redraw this as an anime illustration, never a photograph")


def test_photographic_path_keeps_its_own_clause():
    clause = prompt_style.identity_clause(subject="person", drawn=False)
    assert clause.startswith("keep the same person")
    assert "redraw this as" not in clause


def test_editor_is_told_it_is_a_redraw_not_a_retouch():
    drawn = gpt._system_for(editing=True, lettering=False, drawn=True)
    photo = gpt._system_for(editing=True, lettering=False, drawn=False)
    assert "NEW drawing of this person" in drawn
    assert "NEW drawing of this person" not in photo


# ─── Выражение лица ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("clause", [
    prompt_style.IDENTITY_CLAUSE,
    prompt_style.DRAWN_IDENTITY_CLAUSE,
    prompt_style.cast_clause(["Igor", "Anya"]),
])
def test_expression_comes_from_the_scene(clause):
    # Набор снимков почти всегда начинается с улыбки — так люди фотографируются.
    # Без этой оговорки человек улыбался и под дождём, и на драматичном постере.
    assert "do not copy the smile" in clause


# ─── Рисунок, вернувшийся фотографией ────────────────────────────────────────


class FakeResult:
    def __init__(self, data: bytes):
        self.data = data
        self.provider_id = "p"
        self.model = "m"


@pytest.fixture
def guard(monkeypatch):
    """Подмена зрения и исполнителя: проверяем решение, а не сеть."""
    from app.routers import generate as router
    from app.services.generation import operations

    calls: dict = {"runs": []}

    def vision(verdicts):
        answers = list(verdicts)

        async def _look(data):
            return answers.pop(0) if answers else False

        monkeypatch.setattr(router.gpt_service, "looks_photographic", _look)

    async def _run(db, request, prefer=None):
        calls["runs"].append(request.prompt)
        return FakeResult(b"second")

    monkeypatch.setattr(router.generation_core, "run", _run)
    request = operations.GenerationRequest(
        operation=operations.Operation.IMAGE_TO_IMAGE, prompt="anime poster",
    )
    return vision, calls, request


async def test_a_photograph_is_drawn_again(guard):
    from app.routers.generate import _redraw_if_photographic

    vision, calls, request = guard
    vision([True])
    result, prompt = await _redraw_if_photographic(
        None, request, FakeResult(b"first"), "anime poster", prefer=None)
    # Переделали, и на повтор ушло прямое «прошлый кадр вернулся фотографией»:
    # вежливое описание стиля модель уже прочитала и не послушалась.
    assert calls["runs"] and "previous attempt came back as a photograph" in calls["runs"][0]
    assert result.data == b"second"
    assert prompt.endswith(prompt_style.REDRAW_HARDER)


async def test_a_drawing_is_left_alone(guard):
    from app.routers.generate import _redraw_if_photographic

    vision, calls, request = guard
    vision([False])
    result, prompt = await _redraw_if_photographic(
        None, request, FakeResult(b"first"), "anime poster", prefer=None)
    # Лишний повтор — это наши деньги и чужое ожидание.
    assert calls["runs"] == []
    assert result.data == b"first"
    assert prompt == "anime poster"


async def test_a_failed_retry_still_returns_a_picture(monkeypatch, guard):
    from app.routers import generate as router
    from app.routers.generate import _redraw_if_photographic

    vision, _, request = guard
    vision([True])

    async def _boom(db, request, prefer=None):
        raise RuntimeError("провайдер лёг")

    monkeypatch.setattr(router.generation_core, "run", _boom)
    result, prompt = await _redraw_if_photographic(
        None, request, FakeResult(b"first"), "anime poster", prefer=None)
    # Человек заплатил и ждёт картинку: неудачный повтор не повод отдать ничего.
    assert result.data == b"first"
    assert prompt == "anime poster"
