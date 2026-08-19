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


def test_preset_keys_survive_the_style_map():
    """Якорь, присланный идентификатором, обязан дожить до пресета.

    Приложение шлёт то, что вернул разбор фразы, — `scene_cozy`, `semi_real_3d`,
    — а таблица подписей их не знала и молча подставляла умолчание. Человек при
    этом видел выбранный стиль в ответах, а на кадре был мультяшный якорь.
    """
    for key in prompt_style.PRESETS:
        assert prompt_style.map_style(key) == key
    # Подписи с кнопок продолжают работать: приложения старых сборок шлют их.
    assert prompt_style.map_style("Cartoon 3D") == "3d_cartoon"
    assert prompt_style.map_style("Cozy scene") == "scene_cozy"
    assert prompt_style.map_style("что-то своё") == prompt_style.DEFAULT_STYLE


# ─── Постер живёт надписью ───────────────────────────────────────────────────

def test_lettering_removes_the_ban_on_letters():
    """Постеру нельзя запрещать буквы — это отмена самого постера.

    Оба негативных списка кончались на «text, letters, words, captions», и с
    ними ни одна модель надпись не нарисует. Человек просил своё имя и слова
    про баскетбол, а получил фотографию баскетболиста: слова были запрещены
    дважды — в инструкции сборщику и здесь.
    """
    for key in ("anime", "realistic"):
        plain = prompt_style.negative_for(key)
        assert "text, letters, words" in plain
        lettered = prompt_style.negative_for(key, lettering=True)
        assert "letters" not in lettered
        assert "small unreadable text" not in lettered
        # Всё остальное на месте: снимаем запрет на буквы, а не весь негатив.
        assert "watermarks" in lettered or "watermark" in lettered


def test_poster_instruction_asks_for_type_and_forbids_a_scene():
    from app.services import gpt

    poster = gpt._system_for(editing=True, lettering=True)
    assert "MUST appear in the image" in poster
    assert "Do not invent a location" in poster
    assert "No letters or text in the image" not in poster

    portrait = gpt._system_for(editing=True, lettering=False)
    assert "No letters or text in the image" in portrait
    assert "POSTER" not in portrait


def test_only_lettering_intents_get_the_poster_rules():
    from app.services import gpt

    assert gpt.LETTERING_INTENTS == {"poster", "card"}


def test_a_poster_does_not_get_a_landscape_from_its_style():
    """Стиль решает, чем нарисовано, а не что нарисовано вокруг.

    Якорь аниме просит «warm hand-painted backgrounds with nostalgic pastoral
    mood» — и вокруг вертикального постера вырастал нарисованный лес, которого
    никто не заказывал.
    """
    poster = prompt_style.assemble("Nikita, NBA champion", style_key="anime",
                                   is_text=False, lettering=True)
    assert "pastoral" not in poster
    assert "detailed background" not in poster
    assert "no scenery and no landscape behind the lettering" in poster
    # Сам стиль остаётся: вырезается фон, а не техника рисования.
    assert "clean cel shading" in poster

    plain = prompt_style.assemble("a boy on a road", style_key="anime", is_text=False)
    assert "pastoral" in plain
    assert "no scenery" not in plain


def test_a_poster_takes_only_the_person_from_the_photo():
    """Редактор бережёт кадр целиком — на постере это лишнее.

    Комната человека оставалась на месте, а при переходе в 16:9 её дорисовывали
    вширь: выходила та же комната, только шире. Про фон в требовании сохранить
    человека не было ни слова.
    """
    poster = prompt_style.assemble("Nikita, NBA champion", style_key="anime",
                                   is_text=False, editing=True, lettering=True)
    assert "cut them out and discard" in poster
    assert "Never extend, outpaint or widen" in poster
    # Сам человек по-прежнему обязан остаться узнаваемым.
    assert "recognisably them by face shape" in poster

    portrait = prompt_style.assemble("on a rooftop", style_key="anime",
                                     is_text=False, editing=True)
    assert "cut them out" not in portrait


def test_a_drawn_style_redraws_the_person_instead_of_tracing_them():
    """Человек просил аниме — он должен узнать себя, а не увидеть свой снимок,
    обведённый по контуру.

    «Same clothing» здесь стояло отдельной строкой, и на постере NBA человек
    оказывался в той же серой футболке, в которой сфотографировался дома: мы
    сами это и просили.
    """
    drawn = prompt_style.identity_clause(subject="person", drawn=True)
    assert "redrawn as a character in this style" in drawn
    assert "not a traced photograph" in drawn
    assert "same clothing" not in drawn
    assert "recognisably them by face shape" in drawn

    # На фотопути ничего не меняется: там обещано сходство, а не перерисовка.
    photo = prompt_style.identity_clause(subject="person")
    assert "same face and facial features" in photo


# ─── Образец стиля ───────────────────────────────────────────────────────────

def test_a_style_sample_is_not_a_person():
    """«Сделай меня в стилистике вот этого постера».

    Без отдельной строки модель делает с лишней картинкой единственное, что
    умеет: переносит из неё людей и предметы. Человек прикладывал постер ради
    палитры и набора, а получал чужого баскетболиста в своём кадре.
    """
    with_ref = prompt_style.assemble("Nikita on a poster", style_key="anime",
                                     is_text=False, editing=True, style_ref=True)
    assert "STYLE SAMPLE, not a person" in with_ref
    assert "Never copy the people, faces, objects" in with_ref
    # Требование сохранить самого человека стоит раньше образца: сначала кто,
    # потом как.
    assert with_ref.index("redrawn as a character") < with_ref.index("STYLE SAMPLE")

    without = prompt_style.assemble("Nikita on a poster", style_key="anime",
                                    is_text=False, editing=True)
    assert "STYLE SAMPLE" not in without


def test_your_own_work_is_edited_not_redrawn():
    """«Вот этот кадр, но поменяй фон».

    Требование «сохрани лицо с фотографии» тут неуместно вдвойне: лица на
    рисунке нет — есть его изображение, и тянуть к фотографической точности
    значит уводить кадр обратно в фотографию.
    """
    again = prompt_style.assemble("change the background to a night city",
                                  style_key="anime", is_text=False,
                                  editing=True, redraw=True)
    assert "own earlier result" in again
    assert "change only what is asked for" in again
    assert "keep the same person from the reference photo" not in again
    assert "redrawn as a character" not in again
