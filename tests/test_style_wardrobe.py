"""Эксперимент: одежда с витринного кадра стиля.

Флаг выключен — промпт стиля прежний. Включён и кадр приложен — в промпте
стоит требование взять одежду с образца и подогнать под пол человека, сразу
за требованием сохранить самого человека.
"""
from __future__ import annotations

from types import SimpleNamespace

from app.services import content_gen, prompt_style


def _style():
    return SimpleNamespace(prompt_template={"text": "the person in a long dark coat", "anchor": "realistic"})


def test_default_prompt_untouched() -> None:
    prompt, _ = content_gen.build_style_prompt(_style(), editing=True)
    assert "STYLE SAMPLE" not in prompt
    assert "outfit comes from" not in prompt


def test_wardrobe_clause_follows_identity() -> None:
    prompt, _ = content_gen.build_style_prompt(_style(), editing=True, wardrobe_from_sample=True)
    assert "outfit comes from" in prompt
    assert "equivalent outfit for a man" in prompt
    identity = prompt.index(prompt_style.IDENTITY_CLAUSE[:40])
    wardrobe = prompt.index(prompt_style.WARDROBE_FROM_SAMPLE_CLAUSE[:40])
    scene = prompt.index("long dark coat")
    assert identity < wardrobe < scene
