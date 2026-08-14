"""Поведение сборщика промпта, когда GPT недоступен.

Это самая тихая часть системы: ошибка здесь не падает, а превращается в
списанный TOONTOON и картинку не по запросу. Проверяем два обещания — не отдавать
модели непереведённый текст и не сдаваться после первой же осечки сети.

    PYTHONPATH=. .venv/bin/python -m pytest tests -q
"""
from __future__ import annotations

import httpx
import pytest

from app.services import content_gen, gpt


# ─── Отказ вместо непереведённого промпта ────────────────────────────────────


@pytest.mark.parametrize(
    "text,expected",
    [
        ("бабушка с котом на веранде", True),
        ("a grandmother with a cat", False),
        ("café au lait", False),          # латиница с диакритикой — модель поймёт
        ("cat 🐈 on a veranda", False),   # эмодзи буквами не считаются
        ("猫", True),
        ("", False),
    ],
)
def test_detects_text_the_builder_cannot_translate(text: str, expected: bool) -> None:
    assert content_gen._has_non_latin_letters(text) is expected


@pytest.fixture
def gpt_is_down(monkeypatch: pytest.MonkeyPatch):
    """GPT возвращает пустой промпт — как при упавшем ключе или таймауте."""
    async def _empty(**kwargs):
        return "", ""

    monkeypatch.setattr(gpt, "build_prompt", _empty)


async def test_russian_request_is_refused_when_gpt_is_down(gpt_is_down) -> None:
    with pytest.raises(content_gen.PromptUnavailable):
        await content_gen.build_prompt_for(
            tile=None, answers={}, free_text="бабушка с котом пьёт чай", style=None
        )


async def test_english_request_still_falls_back_to_the_mechanical_builder(gpt_is_down) -> None:
    prompt, negative = await content_gen.build_prompt_for(
        tile=None, answers={}, free_text="a grandmother with a cat", style=None
    )
    assert "a grandmother with a cat" in prompt
    assert prompt.startswith("vibrant 3D cartoon render")
    assert negative


async def test_refusal_also_looks_at_tile_answers(gpt_is_down) -> None:
    with pytest.raises(content_gen.PromptUnavailable):
        await content_gen.build_prompt_for(
            tile=None, answers={"mood": "тёплое утро"}, free_text=None, style=None
        )


# ─── Пересдача при быстром отказе OpenAI ─────────────────────────────────────


def _reply(text: str) -> httpx.Response:
    return httpx.Response(
        200, json={"choices": [{"message": {"content": text}}]},
        request=httpx.Request("POST", gpt._OPENAI_CHAT_URL),
    )


@pytest.fixture(autouse=True)
def no_retry_pause(monkeypatch: pytest.MonkeyPatch):
    async def _instant(_seconds):
        return None

    monkeypatch.setattr(gpt.asyncio, "sleep", _instant)


def _mock_post(monkeypatch: pytest.MonkeyPatch, responses: list):
    """Каждый вызов POST отдаёт следующий элемент: Response или исключение."""
    calls = {"n": 0}

    async def _post(self, url, **kwargs):
        item = responses[calls["n"]]
        calls["n"] += 1
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(httpx.AsyncClient, "post", _post)
    return calls


async def test_rate_limit_is_retried_once(monkeypatch: pytest.MonkeyPatch) -> None:
    limited = httpx.Response(
        429, request=httpx.Request("POST", gpt._OPENAI_CHAT_URL)
    )
    calls = _mock_post(monkeypatch, [limited, _reply("a scene")])

    assert await gpt._call([{"role": "user", "content": "hi"}]) == "a scene"
    assert calls["n"] == 2


async def test_dropped_connection_is_retried_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _mock_post(monkeypatch, [httpx.ConnectError("boom"), _reply("a scene")])

    assert await gpt._call([{"role": "user", "content": "hi"}]) == "a scene"
    assert calls["n"] == 2


async def test_timeout_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """Таймаут уже съел свои 20 секунд — второй заход рискует остаться без ответа."""
    calls = _mock_post(monkeypatch, [httpx.ReadTimeout("slow"), _reply("a scene")])

    with pytest.raises(httpx.ReadTimeout):
        await gpt._call([{"role": "user", "content": "hi"}])
    assert calls["n"] == 1


async def test_bad_request_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """400 — это наша ошибка в запросе, повтор её не исправит."""
    bad = httpx.Response(400, request=httpx.Request("POST", gpt._OPENAI_CHAT_URL))
    calls = _mock_post(monkeypatch, [bad, _reply("a scene")])

    with pytest.raises(httpx.HTTPStatusError):
        await gpt._call([{"role": "user", "content": "hi"}])
    assert calls["n"] == 1


async def test_two_failures_give_up(monkeypatch: pytest.MonkeyPatch) -> None:
    down = httpx.Response(503, request=httpx.Request("POST", gpt._OPENAI_CHAT_URL))
    calls = _mock_post(monkeypatch, [down, down])

    with pytest.raises(httpx.HTTPStatusError):
        await gpt._call([{"role": "user", "content": "hi"}])
    assert calls["n"] == 2


# ─── Путь с фотографией: промпт другого жанра ────────────────────────────────


def test_photo_path_starts_with_keeping_the_person() -> None:
    """Порядок здесь — не косметика. Модель читает промпт слева направо, и
    сходство лица должно стоять раньше стиля, иначе это предложение
    нарисовать заново."""
    from app.services import prompt_style

    prompt = prompt_style.assemble(
        "on a sunlit veranda", style_key="realistic", is_text=False, editing=True
    )
    assert prompt.startswith("keep the same person from the reference photo")


def test_drawing_path_is_left_alone() -> None:
    from app.services import prompt_style

    prompt = prompt_style.assemble(
        "a cat on a veranda", style_key="3d_cartoon", is_text=False
    )
    assert "keep the same person" not in prompt
    assert prompt.startswith("vibrant 3D cartoon render")


def test_photo_without_a_style_is_realistic_not_cartoon(gpt_is_down) -> None:
    """Человек на снимке подпадает под «живой субъект», и автовыбор уводил
    каждое фото в мультяшный якорь независимо от того, что обещал экран."""
    prompt, _ = content_gen.build_prompt(
        tile=None, answers={}, free_text="on a rooftop at sunset", style=None, editing=True
    )
    assert "hyperrealistic photographic render" in prompt
    assert "3D cartoon render" not in prompt


@pytest.mark.parametrize("guards,editing", [("PHOTO_VISUAL_GUARDS", True),
                                            ("OPENAI_VISUAL_GUARDS", False)])
def test_guards_are_chosen_by_path(guards: str, editing: bool) -> None:
    from app.services import prompt_style

    assert prompt_style.guards_for(editing=editing) == getattr(prompt_style, guards)
