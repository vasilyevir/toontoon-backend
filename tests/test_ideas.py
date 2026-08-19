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


async def test_four_ideas_come_back_as_plain_lines(answered):
    answered("Swap the palette for neon blue and electric violet\n"
             "Drop the background blocks and leave a single deep shadow\n"
             "Dress him in a premium tailored jacket instead of the jersey\n"
             "Add motion blur behind the ball for a sense of speed")
    ideas = await gpt.next_step_ideas(prompt=PROMPT)
    assert len(ideas) == 4
    assert ideas[0].startswith("Swap the palette")


async def test_numbering_and_bullets_are_stripped(answered):
    """Модель нумерует, сколько её ни проси. Это чинится кодом, а не промптом."""
    answered('1. Swap the palette for neon blue\n'
             '- Drop the background blocks\n'
             '• Dress him in a tailored jacket\n'
             '4) "Add motion blur behind the ball"')
    ideas = await gpt.next_step_ideas(prompt=PROMPT)
    assert ideas == [
        "Swap the palette for neon blue",
        "Drop the background blocks",
        "Dress him in a tailored jacket",
        "Add motion blur behind the ball",
    ]


async def test_more_than_four_is_cut(answered):
    answered("\n".join(f"Idea number {i} about the colours" for i in range(9)))
    assert len(await gpt.next_step_ideas(prompt=PROMPT)) == 4


async def test_no_prompt_no_ideas(answered):
    """Кадра нет — предлагать нечего, и выдумывать мы не станем."""
    answered("Swap the palette for neon blue")
    assert await gpt.next_step_ideas(prompt="   ") == []


async def test_a_refusal_is_not_a_failure(monkeypatch):
    """Без идей экран живёт, без кадра — нет. Молчание честнее пятисотки."""
    async def _boom(messages, **kwargs):
        raise RuntimeError("vendor down")
    monkeypatch.setattr(gpt, "_call", _boom)
    assert await gpt.next_step_ideas(prompt=PROMPT) == []


def test_the_instruction_demands_finished_sentences():
    assert "finished instruction" in gpt._IDEAS_SYSTEM
    assert "Eight to sixteen words" in gpt._IDEAS_SYSTEM
    assert "changes something different" in gpt._IDEAS_SYSTEM
