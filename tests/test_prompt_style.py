"""Сборка промпта — то, что уходит в модель на каждом запросе.

Ошибка здесь не падает и не логируется: промпт просто становится хуже, а
заметно это через месяц по картинкам. Поэтому постоянные блоки проверяются
дословно.

    PYTHONPATH=. .venv/bin/python -m pytest tests -q
"""
from __future__ import annotations

import pytest

from app.services import card_prompts, picture_prompts, prompt_style, video_prompts


# Бренд-фильтр режет уже собранную строку, поэтому любое слово из его списка,
# случайно попавшее в наш собственный текст, тихо оттуда исчезает. Так мы
# полгода отправляли «ray-traced global ,» вместо указания на просчёт
# освещения: студия Illumination стоит в фильтре рядом с Pixar и Disney.
CONSTANT_BLOCKS = [
    ("technical", prompt_style._TECHNICAL),
    ("layout", prompt_style.LAYOUT_BLOCK),
    ("appeal", prompt_style.APPEAL_BLOCK),
    ("quality", prompt_style.QUALITY_BLOCK),
    ("card layout+technical", card_prompts._LAYOUT_TECHNICAL),
    ("picture technical", picture_prompts._TECHNICAL),
    ("video technical", video_prompts.TECHNICAL),
    ("video technical inline", video_prompts._TECHNICAL_INLINE),
]


@pytest.mark.parametrize(
    "name,block", CONSTANT_BLOCKS, ids=[n for n, _ in CONSTANT_BLOCKS]
)
def test_constant_blocks_survive_the_brand_filter(name: str, block: str) -> None:
    assert prompt_style.strip_brands(block) == block


@pytest.mark.parametrize("key", sorted(prompt_style.PRESETS))
def test_anchors_survive_the_brand_filter(key: str) -> None:
    preset = prompt_style.PRESETS[key]
    for part in ("anchor", "technical"):
        assert prompt_style.strip_brands(preset[part]) == preset[part]


def test_assembled_prompt_keeps_anchor_first_and_technical_last() -> None:
    prompt = prompt_style.assemble(
        "a cat on a veranda", style_key="3d_cartoon", is_text=False
    )
    assert prompt.startswith("vibrant 3D cartoon render")
    assert prompt.endswith("soft depth of field with gentle background bokeh")
    assert "a cat on a veranda" in prompt


def test_layout_block_is_added_only_for_text_tiles() -> None:
    scene = "a cat on a veranda"
    with_text = prompt_style.assemble(scene, style_key="3d_cartoon", is_text=True)
    without = prompt_style.assemble(scene, style_key="3d_cartoon", is_text=False)
    assert prompt_style.LAYOUT_BLOCK in with_text
    assert prompt_style.LAYOUT_BLOCK not in without


def test_brand_filter_still_removes_studio_names() -> None:
    assert "Pixar" not in prompt_style.strip_brands("a Pixar style cat")
    assert "Disney" not in prompt_style.strip_brands("a Disney princess")
