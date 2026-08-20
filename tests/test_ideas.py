"""Что предложить сделать с готовым кадром.

Самое дорогое в уточнении — не касание, а формулировка: на вопрос «что должно
быть на фоне?» человек должен придумать ответ с нуля. Выбрать легче, чем
сочинить, поэтому после кадра приходят четыре готовые правки.

Ошибка здесь тихая: пустой ответ модели или её привычка нумеровать строки
превращают ряд предложений в мусор под картинкой.

    PYTHONPATH=. .venv/bin/python -m pytest tests -q
"""
from __future__ import annotations

import pytest

from app.services import gpt


@pytest.fixture
def answered(monkeypatch):
    def reply(payload: str):
        async def _call(messages, **kwargs):
            return payload
        monkeypatch.setattr(gpt, "_call", _call)
    return reply


PROMPT = "anime illustration style, a poster of Nikita against blocks of white, red and blue"


async def test_a_result_gets_a_word_and_four_ideas(answered):
    """Разговор не должен заканчиваться картинкой.

    Человек получил кадр и остаётся один на один с пустым полем ввода. Вопрос
    «хотите что-нибудь с ней сделать?» стоит дешевле любой кнопки и работает
    лучше — на него отвечают.
    """
    answered("That poster came out bold. Want to push it further?\n"
             "Swap the palette for neon blue and electric violet\n"
             "Drop the background blocks and leave a single deep shadow\n"
             "Dress him in a premium tailored jacket instead of the jersey\n"
             "Add motion blur behind the ball for a sense of speed")
    remark, ideas = await gpt.next_step_ideas(prompt=PROMPT)
    assert remark.endswith("?")
    assert len(ideas) == 4
    assert ideas[0].startswith("Swap the palette")


async def test_numbering_and_bullets_are_stripped(answered):
    """Модель нумерует, сколько её ни проси. Это чинится кодом, а не промптом."""
    answered('Nice one. Want to take it further?\n'
             '1. Swap the palette for neon blue\n'
             '- Drop the background blocks\n'
             '• Dress him in a tailored jacket\n'
             '4) "Add motion blur behind the ball"')
    _, ideas = await gpt.next_step_ideas(prompt=PROMPT)
    assert ideas == [
        "Swap the palette for neon blue",
        "Drop the background blocks",
        "Dress him in a tailored jacket",
        "Add motion blur behind the ball",
    ]


async def test_more_than_four_is_cut(answered):
    answered("\n".join(f"Idea number {i} about the colours" for i in range(9)))
    _, ideas = await gpt.next_step_ideas(prompt=PROMPT)
    assert len(ideas) == 4


async def test_no_prompt_no_ideas(answered):
    """Кадра нет — предлагать нечего, и выдумывать мы не станем."""
    answered("Swap the palette for neon blue")
    assert await gpt.next_step_ideas(prompt="   ") == ("", [])


async def test_a_refusal_is_not_a_failure(monkeypatch):
    """Без идей экран живёт, без кадра — нет. Молчание честнее пятисотки."""
    async def _boom(messages, **kwargs):
        raise RuntimeError("vendor down")
    monkeypatch.setattr(gpt, "_call", _boom)
    assert await gpt.next_step_ideas(prompt=PROMPT) == ("", [])


def test_the_instruction_opens_the_door_to_another_go():
    """Иначе выходит лента кадров: картинка, картинка, картинка — и молчание."""
    assert "ending in a question that" in gpt._IDEAS_SYSTEM
    assert "not worth reading" in gpt._IDEAS_SYSTEM


def test_the_instruction_demands_finished_sentences():
    assert "finished instruction" in gpt._IDEAS_SYSTEM
    assert "Eight to sixteen words" in gpt._IDEAS_SYSTEM
    assert "changes something different" in gpt._IDEAS_SYSTEM


# ─── Идеи по свежеприложенному снимку ────────────────────────────────────────

def _jpeg() -> bytes:
    """Настоящий однопиксельный JPEG: идеям по снимку нужно его уменьшить."""
    import io
    from PIL import Image
    out = io.BytesIO()
    Image.new("RGB", (8, 8), (120, 90, 60)).save(out, format="JPEG")
    return out.getvalue()


async def test_a_photo_gets_a_remark_and_four_ideas(answered):
    """Снимок — это уже просьба, просто без слов.

    Разговор начинается с ответа на показанное, а не со списка услуг: первая
    строка про сам кадр, остальные — что с ним сделать.
    """
    answered("Lovely soft window light on that one\n"
             "Turn me into a vibrant comic book character\n"
             "Put me on a rooftop at sunset in cinematic light\n"
             "Make an anime poster of me in white and blue\n"
             "Create a soft studio portrait against pastel grey")
    remark, ideas = await gpt.photo_remark_and_ideas(_jpeg())
    assert remark == "Lovely soft window light on that one"
    assert len(ideas) == 4
    assert ideas[0].startswith("Turn me into")


async def test_no_photo_no_answer(answered):
    answered("Turn me into a comic book character")
    assert await gpt.photo_remark_and_ideas(b"") == ("", [])


async def test_vision_refusal_is_not_a_failure(monkeypatch):
    async def _boom(messages, **kwargs):
        raise RuntimeError("vision down")
    monkeypatch.setattr(gpt, "_call", _boom)
    assert await gpt.photo_remark_and_ideas(_jpeg()) == ("", [])


def test_the_photo_instruction_demands_this_picture():
    """Идея — и реплика — подходящие любому снимку, не читаются.

    «Great portrait» годится для чего угодно и звучит как лесть; сказать про
    свет из окна — значит посмотреть.
    """
    assert "reads as flattery" in gpt._PHOTO_IDEAS_SYSTEM
    assert "must fit this one" in gpt._PHOTO_IDEAS_SYSTEM
    assert "never invent a person" in gpt._PHOTO_IDEAS_SYSTEM
    assert "Four different directions" in gpt._PHOTO_IDEAS_SYSTEM


# ─── Разбор набора для профиля ───────────────────────────────────────────────

async def test_the_set_is_judged_photo_by_photo(answered):
    """Набор решает всё, что будет дальше, поэтому смотрим до сборки.

    Двадцать кадров в одном свитере у одной стены дают профиль, который считает
    свитер и стену частью человека, и заметно это станет на десятой генерации.
    """
    answered('{"photos": [{"index": 1, "ok": true, "reason": ""},'
             ' {"index": 2, "ok": false, "reason": "two people in the frame"}],'
             ' "missing": ["one where you are smiling", "one in daylight"]}')
    out = await gpt.review_profile_photos([_jpeg(), _jpeg()])
    assert out["photos"][0] == {"index": 1, "ok": True, "reason": ""}
    assert out["photos"][1]["reason"] == "two people in the frame"
    assert out["missing"] == ["one where you are smiling", "one in daylight"]


async def test_no_photos_no_verdict(answered):
    answered('{"photos": [], "missing": []}')
    assert await gpt.review_profile_photos([]) == {"photos": [], "missing": [], "chosen": []}


async def test_a_blind_reviewer_does_not_block_the_profile(monkeypatch):
    """Не посмотрели — не мешаем.

    Отказать человеку в профиле из-за того, что зрение недоступно, значит
    наказать его за нашу неисправность.
    """
    async def _boom(messages, **kwargs):
        raise RuntimeError("vision down")
    monkeypatch.setattr(gpt, "_call", _boom)
    assert await gpt.review_profile_photos([_jpeg()]) == {"photos": [], "missing": [], "chosen": []}


async def test_more_verdicts_than_photos_are_cut(answered):
    """Модель иногда придумывает лишние строки. Их не должно быть больше, чем
    снимков: человек ищет глазами свою фотографию, а не строку в списке."""
    answered('{"photos": [{"index": 1, "ok": true}, {"index": 2, "ok": true},'
             ' {"index": 3, "ok": false, "reason": "blurry"}], "missing": []}')
    out = await gpt.review_profile_photos([_jpeg()])
    assert len(out["photos"]) == 1


def test_the_review_knows_what_a_good_set_is():
    assert "exactly one person in the frame" in gpt._PROFILE_REVIEW_SYSTEM
    assert "not the same clothes and wall in every frame" in gpt._PROFILE_REVIEW_SYSTEM
    assert "three-quarter" in gpt._PROFILE_REVIEW_SYSTEM


async def test_the_working_set_is_picked_from_the_whole_pile(answered):
    """Хранить пятнадцать и отдавать пятнадцать — разные решения.

    В кадр уезжает отобранное: набор, покрывающий человека без повторов. Лучший
    снимок идёт первым — он же уедет один, если отдавать решено один.
    """
    answered('{"photos": [{"index": 1, "ok": true}, {"index": 2, "ok": true},'
             ' {"index": 3, "ok": true}], "missing": [], "chosen": [3, 1]}')
    out = await gpt.review_profile_photos([_jpeg(), _jpeg(), _jpeg()])
    assert out["chosen"] == [3, 1]


async def test_a_made_up_number_is_dropped(answered):
    """Номера приходят от модели, и чужой индекс означал бы чужую фотографию
    в кадре у человека."""
    answered('{"photos": [{"index": 1, "ok": true}], "missing": [],'
             ' "chosen": [1, 9, "два", 1]}')
    out = await gpt.review_profile_photos([_jpeg()])
    assert out["chosen"] == [1]


async def test_the_working_set_has_a_ceiling(answered):
    """Шесть — предел смысла, а не вендора: дальше идут повторы того, что уже
    покрыто, и каждый лишний референс это ещё один шанс усреднить черты."""
    answered('{"photos": [], "missing": [], "chosen": [1,2,3,4,5,6,7,8]}')
    out = await gpt.review_profile_photos([_jpeg()] * 8)
    assert len(out["chosen"]) == gpt.MAX_REFERENCE_PHOTOS
