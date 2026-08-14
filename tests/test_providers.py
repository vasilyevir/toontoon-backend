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


# ─── Kling: единственный, кому можно сказать «сохрани лицо» ──────────────────


@pytest.fixture
def kling_keys(monkeypatch: pytest.MonkeyPatch):
    from app.services.generation.providers import kling
    monkeypatch.setattr(kling.settings, "kling_api_key", "")
    monkeypatch.setattr(kling.settings, "kling_access_key", "ak-test")
    monkeypatch.setattr(kling.settings, "kling_secret_key", "sk-test")
    monkeypatch.setattr(kling.settings, "kling_poll_interval", 0.0)
    return kling


@pytest.fixture
def kling_api(monkeypatch: pytest.MonkeyPatch, kling_keys) -> dict:
    """Задача создаётся, первый опрос — «считается», второй — готово."""
    seen: dict = {"polls": 0}

    async def _post(self, url, **kwargs):
        seen["create_url"] = url
        seen["body"] = kwargs.get("json")
        seen["headers"] = kwargs.get("headers")
        return httpx.Response(200, json={"code": 0, "data": {"task_id": "t-1"}},
                              request=httpx.Request("POST", url))

    async def _get(self, url, **kwargs):
        if url.endswith(".png"):                      # забор готовой картинки
            return httpx.Response(200, content=_png(), request=httpx.Request("GET", url))
        seen["polls"] += 1
        done = seen["polls"] > 1
        body = {"code": 0, "data": {
            "task_status": "succeed" if done else "processing",
            "task_result": {"images": [{"url": "https://cdn.kling/out.png"}]} if done else {},
        }}
        return httpx.Response(200, json=body, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", _post)
    monkeypatch.setattr(httpx.AsyncClient, "get", _get)
    return seen


PHOTO = b"\x89PNG\r\n\x1a\nphoto-bytes"


async def _run_photo(kling, model: str):
    kling.settings.kling_image_model = model
    return await kling.KlingProvider().run(
        _request(operation=Operation.IMAGE_TO_IMAGE, image=PHOTO, image_mime="image/png")
    )


async def test_second_generation_gets_the_explicit_face_knob(kling_api: dict, kling_keys) -> None:
    result = await _run_photo(kling_keys, "kling-v2-1")

    import base64
    body = kling_api["body"]
    # Снимок уходит base64 в теле — не ссылкой на сторонний хост.
    assert body["image"] == base64.b64encode(PHOTO).decode()
    assert body["image_reference"] == "face"
    assert body["human_fidelity"] == kling_keys.settings.kling_human_fidelity
    assert result.data == _png()
    assert result.provider_id == "kling"


async def test_third_generation_uses_the_same_face_reference(kling_api: dict, kling_keys) -> None:
    """Третья версия принимает снимок тем же полем, что и вторая.

    Нумерованный `image_1` из сторонних описаний API принимает с кодом 200 и
    молча игнорирует — на выходе другой человек. Проверено вживую, поэтому
    тест закрепляет рабочую схему, а не документацию.
    """
    await _run_photo(kling_keys, "kling-v3")

    import base64
    body = kling_api["body"]
    assert body["image"] == base64.b64encode(PHOTO).decode()
    assert body["image_reference"] == "face"
    assert "image_1" not in body


async def test_text_only_request_carries_no_reference_fields(kling_api: dict, kling_keys) -> None:
    await kling_keys.KlingProvider().run(_request())

    body = kling_api["body"]
    assert "image" not in body and "image_reference" not in body


async def test_token_is_a_signed_jwt_from_the_access_key(kling_api: dict, kling_keys) -> None:
    import jwt as pyjwt
    await kling_keys.KlingProvider().run(_request())

    token = kling_api["headers"]["Authorization"].removeprefix("Bearer ")
    claims = pyjwt.decode(token, "sk-test", algorithms=["HS256"])
    assert claims["iss"] == "ak-test"
    assert claims["exp"] > claims["nbf"]
    # Подпись чужим ключом не проходит — иначе секрет не участвует.
    with pytest.raises(pyjwt.InvalidSignatureError):
        pyjwt.decode(token, "wrong", algorithms=["HS256"])


async def test_single_key_console_sends_the_key_as_is(kling_api: dict, kling_keys) -> None:
    """Новая консоль выдаёт один ключ — он идёт в заголовок без JWT."""
    kling_keys.settings.kling_api_key = "api-key-kling-test"
    try:
        await kling_keys.KlingProvider().run(_request())
    finally:
        kling_keys.settings.kling_api_key = ""

    assert kling_api["headers"]["Authorization"] == "Bearer api-key-kling-test"


async def test_adapter_is_skipped_without_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.generation.providers import kling
    monkeypatch.setattr(kling.settings, "kling_api_key", "")
    monkeypatch.setattr(kling.settings, "kling_access_key", "")
    monkeypatch.setattr(kling.settings, "kling_secret_key", "")
    assert kling.KlingProvider().available() is False


async def test_timeout_becomes_a_refusal(monkeypatch: pytest.MonkeyPatch, kling_keys) -> None:
    """Молчащая задача обязана стать отказом: реестр возьмёт следующего,
    а кошелёк вернёт TOONTOON — вместо того чтобы держать человека в ожидании."""
    monkeypatch.setattr(kling_keys.settings, "kling_poll_timeout", 0.05)

    async def _post(self, url, **kwargs):
        return httpx.Response(200, json={"code": 0, "data": {"task_id": "t-2"}},
                              request=httpx.Request("POST", url))

    async def _get(self, url, **kwargs):
        return httpx.Response(200, json={"code": 0, "data": {"task_status": "processing"}},
                              request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", _post)
    monkeypatch.setattr(httpx.AsyncClient, "get", _get)

    from app.services.generation.operations import GenerationUnavailable
    with pytest.raises(GenerationUnavailable):
        await kling_keys.KlingProvider().run(_request())


async def test_row_model_wins_over_the_setting(kling_api: dict, kling_keys) -> None:
    """Строка реестра решает, каким поколением считать.

    Без этого две конфигурации Kling невозможны: обе брали бы модель из одной
    настройки, и вторая строка отличалась бы только именем.
    """
    kling_keys.settings.kling_image_model = "kling-v2-1"   # настройка говорит одно…
    await kling_keys.KlingProvider("kling").run(          # …а строка — другое
        _request(operation=Operation.IMAGE_TO_IMAGE, image=PHOTO, image_mime="image/png"),
        model="kling-v3",
    )

    assert kling_api["body"]["model_name"] == "kling-v3"


async def test_both_kling_rows_resolve_to_an_adapter() -> None:
    from app.services.generation.registry import ADAPTERS
    assert {"kling", "kling_v2"} <= set(ADAPTERS)
    assert ADAPTERS["kling"].id == "kling"
    assert ADAPTERS["kling_v2"].id == "kling_v2"
