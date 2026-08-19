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
