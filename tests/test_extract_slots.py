"""Разбор сказанного по слотам — то, из-за чего разговор либо слушает, либо нет.

Ошибка здесь не падает: человек просто получает вопрос о том, что уже сказал,
и это выглядит не как сбой, а как невнимательность. Поэтому проверяется не
вызов модели, а обращение с её ответом — то, что мы контролируем.

    PYTHONPATH=. .venv/bin/python -m pytest tests -q
"""
from __future__ import annotations

import json

import pytest

from app.services import gpt


@pytest.fixture
def answered(monkeypatch):
    """Подменить модель заранее заготовленным ответом и запомнить, что ей ушло."""
    sent: dict = {}

    def reply(payload: str):
        async def _call(messages, **kwargs):
            sent["messages"] = messages
            return payload
        monkeypatch.setattr(gpt, "_call", _call)
        return sent

    return reply


ALL_SLOTS = list(gpt.SLOT_MEANING)


async def test_closed_slots_are_asked_about(answered):
    """Техника и формат участвуют в разборе.

    Их тут не было, и «постер в стиле аниме» получал вопрос «как это должно
    выглядеть?» с кнопкой Anime среди вариантов — то есть мы переспрашивали
    ровно то, что человек написал первой же фразой.
    """
    sent = answered('{"technique": "anime", "format": "16:9"}')
    filled = await gpt.extract_slots("постер в стиле аниме, горизонтальный", ALL_SLOTS)
    assert filled == {"technique": "anime", "format": "16:9"}

    listing = sent["messages"][1]["content"]
    # Модели показан не только смысл слота, но и закрытый список — иначе она
    # ответит описанием, а описание мы обязаны выбросить.
    assert "anime = anime or manga illustration" in listing
    assert "16:9" in listing


async def test_free_text_slot_passes_through(answered):
    answered('{"palette": "white and blue", "place": "a basketball court"}')
    filled = await gpt.extract_slots("в бело-синих красках на площадке", ALL_SLOTS)
    assert filled == {"palette": "white and blue", "place": "a basketball court"}


async def test_value_outside_the_list_is_dropped(answered):
    """Описание вместо идентификатора — то же, что молчание.

    «horizontal» сервер не примет, вендор тем более: показать его человеку
    значит записать в поле то, что не подействует.
    """
    answered('{"format": "horizontal", "technique": "japanese cartoon", "place": "a rooftop"}')
    filled = await gpt.extract_slots("что-то горизонтальное", ALL_SLOTS)
    assert filled == {"place": "a rooftop"}


async def test_case_and_padding_are_forgiven(answered):
    """Регистр — оплошность модели, а не другой ответ."""
    answered('{"technique": " Anime ", "format": " 16:9 "}')
    filled = await gpt.extract_slots("аниме, широкий кадр", ALL_SLOTS)
    assert filled == {"technique": "anime", "format": "16:9"}


async def test_a_shrug_is_not_an_answer(answered):
    """«Не важно что надето» — отказ отвечать, а не описание одежды.

    Записанное в поле, оно уедет в картинку требованием, и человек увидит его в
    ответах как свой собственный выбор.
    """
    answered('{"wardrobe": "not important", "place": "a rooftop"}')
    filled = await gpt.extract_slots("не важно что надето, давай на крыше", ALL_SLOTS)
    assert filled == {"place": "a rooftop"}


async def test_genre_echo_is_not_an_answer(answered):
    """«Портрет» в кадрировании — это первое слово фразы, а не ответ.

    Замер по моделям показал это у всех кандидатов: человек говорит «хочу
    портрет в кофейне», и в поле «насколько близко» ложится `portrait`.
    """
    answered('{"framing": "portrait", "place": "a café"}')
    filled = await gpt.extract_slots("хочу портрет в кофейне", ALL_SLOTS)
    assert filled == {"place": "a café"}


async def test_only_pending_slots_come_back(answered):
    """Заполненные поля не переспрашиваются и не перезаписываются.

    Приложение шлёт список незакрытых; ответ про поле вне списка — это ответ
    про то, что человек уже выбрал руками.
    """
    answered('{"technique": "anime", "place": "a rooftop"}')
    filled = await gpt.extract_slots("аниме на крыше", ["place"])
    assert filled == {"place": "a rooftop"}


async def test_broken_answer_is_not_a_failure(answered):
    """Модель отказалась — разговор просто задаёт свои вопросы."""
    answered("I can't help with that.")
    assert await gpt.extract_slots("что угодно", ALL_SLOTS) == {}


async def test_option_ids_match_what_the_server_accepts():
    """Список вариантов — тот же, что сверяет генерация.

    Разъедься они, и разбор начнёт возвращать значения, которые сервер молча
    отбросит: человек увидит выбранный стиль, а подействует умолчание.
    """
    from app.routers.generate import ASPECTS
    from app.services import prompt_style

    assert set(gpt.SLOT_OPTIONS["format"]) == ASPECTS
    assert set(gpt.SLOT_OPTIONS["technique"]) <= set(prompt_style.PRESETS)


# ─── Ответ модели ───────────────────────────────────────────────────────────

def test_empty_content_is_not_a_crash():
    """`content: null` — штатный ответ, а не сбой.

    Так отвечают модели с рассуждением, потратившие весь лимит на размышление.
    Раньше здесь падало исключение: разбор отвечал приложению пятисоткой, а
    сборка промпта уходила в отказ с возвратом TOONTOON — и всё это на ответе,
    который просто означает «ничего не сказал».
    """
    assert gpt._content_of({"choices": [{"message": {"content": None}}]}) == ""
    assert gpt._content_of({"choices": [{"message": {}}]}) == ""
    assert gpt._content_of({}) == ""
    assert gpt._content_of({"choices": [{"message": {"content": "  {}  "}}]}) == "{}"


async def test_empty_answer_gives_no_slots(answered):
    answered("")
    assert await gpt.extract_slots("постер в стиле аниме", ALL_SLOTS) == {}


def test_marketplace_model_is_not_sent_to_openai(monkeypatch):
    """Просьба о модели витрины действует только через витрину.

    `google/gemini-2.5-flash` в прямой OpenAI — это отказ вендора, который
    человек увидит как нашу поломку. Без ключа витрины разбор должен просто
    уйти на общую модель.
    """
    from app.config import settings

    monkeypatch.setattr(settings, "openai_model", "gpt-4o-mini")
    monkeypatch.setattr(settings, "openrouter_text_model", "openai/gpt-4o-mini")
    assert gpt._model_for("google/gemini-2.5-flash", use_router=False) == "gpt-4o-mini"
    assert gpt._model_for("google/gemini-2.5-flash", use_router=True) == "google/gemini-2.5-flash"
    assert gpt._model_for(None, use_router=True) == "openai/gpt-4o-mini"


# ─── Роли приложенных снимков ───────────────────────────────────────────────

def _png() -> bytes:
    """Настоящий однопиксельный PNG: разбору ролей нужно уменьшить картинку."""
    import base64
    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


@pytest.fixture
def sees(monkeypatch):
    """Подменить зрение модели заготовленным ответом и запомнить запрос."""
    sent: dict = {}

    def reply(payload: str):
        async def _call(messages, **kwargs):
            sent["messages"] = messages
            return payload
        monkeypatch.setattr(gpt, "_call", _call)
        return sent

    return reply


async def test_the_poster_is_recognised_as_a_style_sample(sees):
    """Человек прикладывает своё фото и постер и пишет «сделай меня в стилистике
    этого». Порядок файлов об этом не говорит, а роли решают всё: постер,
    принятый за человека, — это чужое лицо в кадре вместо своего.
    """
    sent = sees('["person", "style"]')
    roles = await gpt.reference_roles(
        [(_png(), "image/png"), (_png(), "image/png")], "сделай меня в стилистике этого постера")
    assert roles == ["person", "style"]
    # Модель видит и картинки, и фразу: по фразе она разрешает спорные случаи.
    parts = sent["messages"][1]["content"]
    assert sum(1 for p in parts if p["type"] == "image_url") == 2
    assert "стилистике" in parts[0]["text"]


async def test_a_single_picture_is_not_worth_asking_about(sees):
    sees('["style"]')
    assert await gpt.reference_roles([(_png(), "image/png")], "как здесь") == []


async def test_all_style_is_refused(sees):
    """Кто-то должен быть субъектом. Ответ без человека — это не разбор, а
    отказ, и принимать его нельзя: иначе в кадре не окажется никого."""
    sees('["style", "style"]')
    assert await gpt.reference_roles(
        [(_png(), "image/png"), (_png(), "image/png")], "как здесь") == []


async def test_a_broken_answer_leaves_everything_as_it_came(sees):
    """Не разобрали — оставляем как прислали. Молчание безопаснее догадки."""
    sees("I think the first one is you?")
    assert await gpt.reference_roles(
        [(_png(), "image/png"), (_png(), "image/png")], "как здесь") == []


def test_a_chosen_role_is_not_up_for_discussion():
    """«Оба снимка — люди» это просьба, а не пустое поле.

    Роль человека стоит умолчанием, поэтому пустой список образцов означает и
    «я выбрал обоих людьми», и «я ничего не трогал». Отличает их только флаг —
    без него разбор молча переставлял бы второго человека в образцы и ломал
    ровно тот случай, ради которого человек и расставлял роли руками.
    """
    from app.models.generation import GenerateRequest

    assert GenerateRequest().roles_chosen is False
    assert GenerateRequest(roles_chosen=True).roles_chosen is True


async def test_a_dead_network_is_an_empty_answer(monkeypatch):
    """Разбор необязателен, разговор обязателен.

    Вызов стоял вне `try`, и оборванная сеть поднималась наружу: ход в чате
    отвечал пятисоткой, обе реплики пропадали. А достаточно было задать вопрос —
    ровно так, как разговор вёл бы себя без разбора вовсе.
    """
    import httpx

    async def _dead(messages, **kwargs):
        raise httpx.ConnectError("")

    monkeypatch.setattr(gpt, "_call", _dead)
    assert await gpt.extract_slots("постер в стиле аниме", ALL_SLOTS) == {}
