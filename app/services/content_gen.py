"""Сборка промпта для генератора изображений.

Модуль отвечает только за текст. Какая модель его исполнит — решает реестр
провайдеров (`app/services/generation`, CH-21); раньше выбор модели, скачивание
картинки и складывание её в локальный `uploads/` жили здесь же, и всё это
удалено вместе со старым пайплайном.

Промпт пишет GPT (`gpt.build_prompt`). Механический сборщик ниже — запасной
путь на случай, когда GPT недоступен: он раскладывает текст по тем же слотам,
но переводить не умеет, поэтому запрос не на латинице в этот момент
отклоняется, а не отправляется в модель как есть.
"""
from __future__ import annotations

import unicodedata

from app.models.tile import Tile
from app.services import picture_prompts, prompt_style


class PromptUnavailable(RuntimeError):
    """Нечем перевести запрос на английский — генерировать нельзя.

    Механический сборщик умеет только разложить текст по слотам; переводить он
    не умеет. Раньше на этом месте стояла транслитерация, и запрос «бабушка с
    котом» уезжал в модель как «babushka s kotom» — картинка выходила, TOONTOON
    списывался, а к просьбе она отношения не имела. Отказ с возвратом честнее:
    человек видит «попробуйте через минуту», а не случайную картинку за свои
    деньги.
    """


def _has_non_latin_letters(text: str) -> bool:
    """Есть ли в тексте буквы вне латиницы — кириллица, греческий, CJK и т.п.

    Проверяются именно буквы: эмодзи, цифры и пунктуация не считаются, а
    латиница с диакритикой («café») остаётся допустимой — модель её понимает.
    """
    for ch in text:
        if ch.isalpha() and ord(ch) > 127:
            if not unicodedata.name(ch, "").startswith("LATIN"):
                return True
    return False


def build_prompt(
    *,
    tile: Tile | None,
    answers: dict[str, str],
    free_text: str | None,
    style: str | None,
    editing: bool = False,
) -> tuple[str, str]:
    """Mechanical fallback builder (used when GPT is unavailable).

    Returns ``(prompt, negative_prompt)``. For picture tiles it fills the exact
    per-tile template with our answers; otherwise it assembles style anchor +
    scene + optional layout + technical block. Never emits banned "quality"
    words and never renders greeting text.
    """
    # Picture tiles: fill the per-tile template deterministically (best effort).
    if tile is not None and picture_prompts.is_picture_tile(tile.id):
        tpl = picture_prompts.TEMPLATES[tile.id]
        values: dict[str, str] = {}
        answer_values = [answers[q.id] for q in tile.questions if answers.get(q.id)]
        # Map answers onto placeholders positionally, falling back to defaults.
        names = list(tpl.fields.keys())
        for i, name in enumerate(names):
            if i < len(answer_values) and answer_values[i]:
                values[name] = answer_values[i]
            else:
                values[name] = tpl.fields[name] or "scene"
        prompt = tpl.template
        for name, value in values.items():
            prompt = prompt.replace("{" + name + "}", value)
        return prompt, prompt_style.NEGATIVE_PROMPT

    style_key = "realistic" if editing and not style else prompt_style.map_style(style)
    is_text = prompt_style.is_text_tile(tile.category.value) if tile else False

    scene_parts: list[str] = []
    if tile is not None:
        scene_parts.append(tile.title)
        for question in tile.questions:
            value = answers.get(question.id)
            if not value:
                continue
            if question.id == "text":
                # Greeting text is overlaid later, never drawn by the generator.
                continue
            if question.id == "style":
                # Style is expressed via the anchor, not as a raw scene word.
                continue
            scene_parts.append(value)
    if free_text:
        scene_parts.append(free_text)
    scene_parts.append("warm friendly expression, heartwarming gentle mood")

    scene = ", ".join(p.strip() for p in scene_parts if p and p.strip())
    prompt = prompt_style.assemble(
        scene, style_key=style_key, is_text=is_text, editing=editing
    )
    return prompt, prompt_style.NEGATIVE_PROMPT


def build_style_prompt(style, *, editing: bool = True) -> tuple[str, str]:
    """Промпт стиля из каталога — как написан, без переписывания.

    Витрина показывает конкретный результат, и повторить его можно только тем
    же текстом, которым он сделан. Отдать этот текст GPT «на улучшение» значит
    показывать одно, а генерировать другое; заодно исчезает лишний сетевой
    вызов и лишняя точка отказа на самом ходовом пути продукта.

    Обвязка та же, что и везде: требование сохранить человека впереди, стиль и
    технический блок — вокруг.
    """
    text = (style.prompt_template or {}).get("text", "").strip()
    style_key = (style.prompt_template or {}).get("anchor") or (
        "realistic" if editing else prompt_style.DEFAULT_STYLE
    )
    prompt = prompt_style.assemble(
        text, style_key=style_key, is_text=False, editing=editing,
        subject=(style.prompt_template or {}).get("subject", "person"),
    )
    return prompt, prompt_style.negative_for(style_key)


async def build_prompt_for(
    *,
    tile,
    answers: dict[str, str],
    free_text: str | None,
    style: str | None,
    editing: bool = False,
    intent: str | None = None,
    style_ref: bool = False,
    redraw: bool = False,
    subject: str = "person",
    cast: list[str] | None = None,
) -> tuple[str, str]:
    """Собрать промпт, ничего не генерируя.

    GPT пишет промпт, если ключ есть; иначе работает механический сборщик,
    который не зависит ни от чего.

    Поднимает ``PromptUnavailable``, если GPT недоступен, а запрос написан не
    латиницей: перевести его нечем, а отдавать модели непереведённый текст —
    значит взять деньги за случайную картинку.
    """
    from app.services import gpt as gpt_service

    style = style or answers.get("style")
    prompt, negative = await gpt_service.build_prompt(
        tile=tile,
        answers=answers,
        free_text=free_text,
        style=style,
        editing=editing,
        intent=intent,
        style_ref=style_ref,
        redraw=redraw,
        subject=subject,
        cast=cast,
    )
    if not prompt:
        user_text = " ".join(filter(None, [free_text, *answers.values()]))
        if _has_non_latin_letters(user_text):
            raise PromptUnavailable(
                "Промпт-сборщик недоступен, а запрос не на латинице — перевести нечем"
            )
        prompt, negative = build_prompt(
            tile=tile, answers=answers, free_text=free_text, style=style,
            editing=editing,
        )
    return prompt, negative or ""
