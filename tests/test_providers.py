"""Что именно уходит провайдеру.

Оба адаптера получают один и тот же запрос, но разговаривают с моделями
по-разному: у Pollinations негатив — отдельный параметр, у OpenAI такого
параметра нет вовсе. Тесты держат это различие явным, потому что молчаливая
потеря негатива не падает и не логируется — она просто ухудшает картинки.

    PYTHONPATH=. .venv/bin/python -m pytest tests -q
"""
from __future__ import annotations

import httpx
import pytest

from app.services import prompt_style
from app.services.generation.operations import GenerationRequest, Operation
from app.services.generation.providers import openai_images, pollinations


def _request(**kwargs) -> GenerationRequest:
    base = dict(
        operation=Operation.TEXT_TO_IMAGE,
        prompt="a cat on a veranda",
        negative_prompt=prompt_style.NEGATIVE_PROMPT,
    )
    base.update(kwargs)
    return GenerationRequest(**base)


def _png() -> bytes:
    # Минимальный валидный ответ: адаптеру важно только, что это картинка.
    return b"\x89PNG\r\n\x1a\n" + b"0" * 16


# ─── OpenAI: негатива в API нет, запреты уезжают в положительный промпт ───────


@pytest.fixture
def openai_capture(monkeypatch: pytest.MonkeyPatch) -> dict:
    sent: dict = {}

    async def _post(self, url, **kwargs):
        sent["url"] = url
        sent["json"] = kwargs.get("json")
        sent["data"] = kwargs.get("data")
        import base64
        return httpx.Response(
            200,
            json={"data": [{"b64_json": base64.b64encode(_png()).decode()}]},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", _post)
    monkeypatch.setattr(openai_images.settings, "openai_api_key", "test-key")
    return sent


async def test_guards_ride_along_in_the_positive_prompt(openai_capture: dict) -> None:
    await openai_images.OpenAIImagesProvider().run(_request())

    prompt = openai_capture["json"]["prompt"]
    assert prompt.startswith("a cat on a veranda")
    assert prompt_style.OPENAI_VISUAL_GUARDS in prompt
    # Негатива в теле запроса быть не должно — API его не принимает.
    assert "negative" not in {k.lower() for k in openai_capture["json"]}


async def test_photo_edit_gets_its_own_guards_without_cartoon(openai_capture: dict) -> None:
    """На снимке живого человека общий набор вреден: он начинается со слов
    «friendly non-scary cartoon face» — инструкции против того, что обещает
    экран «AI Photo Studio»."""
    await openai_images.OpenAIImagesProvider().run(
        _request(operation=Operation.IMAGE_TO_IMAGE, image=_png(), image_mime="image/png")
    )

    prompt = openai_capture["data"]["prompt"]
    assert prompt_style.PHOTO_VISUAL_GUARDS in prompt
    assert "cartoon face" not in prompt


# ─── Pollinations: негатив уходит целиком ────────────────────────────────────


@pytest.fixture
def pollinations_capture(monkeypatch: pytest.MonkeyPatch) -> dict:
    sent: dict = {}

    async def _get(self, url, **kwargs):
        sent["url"] = url
        return httpx.Response(
            200, content=_png(), headers={"content-type": "image/png"},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", _get)
    return sent


async def test_full_negative_prompt_reaches_pollinations(pollinations_capture: dict) -> None:
    await pollinations.PollinationsProvider().run(_request())

    from urllib.parse import parse_qs, urlparse
    sent = parse_qs(urlparse(pollinations_capture["url"]).query)["negative"][0]
    assert sent == prompt_style.NEGATIVE_PROMPT
    # Хвост списка — запреты на кадр и на текст в картинке; на прежней обрезке
    # в 500 символов терялись именно они.
    assert sent.endswith("logo")


# ─── Очередь исполнителей ────────────────────────────────────────────────────
#
# Одна плохая секунда сети не должна стоить человеку кадра, а разбирать потом
# приходится по единственной записи в базе: логи живут в консоли и до завтра не
# доживают.

from app.services.generation import registry
from app.services.generation.operations import GenerationResult


class _Adapter:
    """Исполнитель, падающий заданным списком осечек."""

    def __init__(self, *errors: Exception) -> None:
        self.errors = list(errors)
        self.calls = 0

    async def run(self, request, *, model=None) -> GenerationResult:
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return GenerationResult(data=_png(), mime="image/png",
                                provider_id="stub", model=model or "stub")


async def test_a_dropped_connection_is_tried_once_more():
    """Соединение не установилось — значит запрос не ушёл, и повтор бесплатен."""
    adapter = _Adapter(httpx.ConnectError(""))
    result = await registry._attempt("stub", adapter, _request(), "stub-model")
    assert adapter.calls == 2
    assert result.model == "stub-model"


async def test_a_read_timeout_is_not_retried():
    """Вендор работу получил и может её посчитать. Платить дважды за одну
    картинку хуже, чем отказать."""
    adapter = _Adapter(httpx.ReadTimeout("too slow"))
    with pytest.raises(httpx.ReadTimeout):
        await registry._attempt("stub", adapter, _request(), "stub-model")
    assert adapter.calls == 1


async def test_two_dropped_connections_in_a_row_give_up():
    adapter = _Adapter(httpx.ConnectError(""), httpx.ConnectError(""))
    with pytest.raises(httpx.ConnectError):
        await registry._attempt("stub", adapter, _request(), "stub-model")
    assert adapter.calls == 2


# ─── Значения параметров, а не только их имена ───────────────────────────────


def test_unsupported_aspect_becomes_the_nearest_one():
    from app.services.generation.providers.openrouter import _fit

    # GPT Image 2 знает 3:4, но не знает 4:5. Мы смотрели только на имя
    # параметра — и вертикальный портрет уезжал в отказ 400, а очередь тихо
    # отдавала кадр следующему исполнителю: другая модель, другое качество,
    # другая цена, и всё это без единого слова человеку.
    gpt = {"values": ["1:1", "3:2", "2:3", "4:3", "3:4", "16:9", "9:16", "21:9", "auto"]}
    assert _fit("4:5", gpt) == "3:4"


def test_supported_value_goes_through_untouched():
    from app.services.generation.providers.openrouter import _fit

    gemini = {"values": ["1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "9:16", "16:9"]}
    assert _fit("4:5", gemini) == "4:5"


def test_no_value_list_means_no_guessing():
    from app.services.generation.providers.openrouter import _fit

    # Витрина не всегда перечисляет значения. Тогда шлём как есть: подменять
    # то, о чём ничего не известно, — это ошибка на ровном месте.
    assert _fit("4:5", {"type": "enum"}) == "4:5"
    assert _fit("4:5", None) == "4:5"
