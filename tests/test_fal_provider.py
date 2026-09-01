"""Исполнитель fal.ai: очередь, снимки и отказы.

Написано без ключа: настоящий fal здесь не участвует, вместо него поддельный
транспорт. Это не полумера — проверять надо не то, что fal отвечает (это их
забота), а то, что МЫ правильно ставим в очередь, правильно ждём, правильно
кладём снимки и правильно сдаёмся.

С ключом останется проверить одно: совпадает ли форма запроса с настоящей.

    PYTHONPATH=. .venv/bin/python -m pytest tests/test_fal_provider.py -q
"""
from __future__ import annotations

import base64
import json

import httpx
import pytest

from app.config import settings
from app.services.generation.operations import (
    GenerationRequest,
    GenerationUnavailable,
    Operation,
)
from app.services.generation.providers.fal import FalProvider

pytestmark = pytest.mark.asyncio

КАДР = b"\x89PNG\r\n\x1a\n" + b"picture-bytes"


class Поддельный:
    """Очередь fal в трёх ответах: поставили, ждём, готово."""

    def __init__(self, *, статусы=None, ответ=None, отказ_на=None):
        self.статусы = list(статусы or ["COMPLETED"])
        self.ответ = ответ if ответ is not None else {
            "images": [{"url": "https://v3.fal.media/files/x/out.png",
                        "content_type": "image/png"}]
        }
        self.отказ_на = отказ_на or {}
        self.запросы: list[tuple[str, str, dict]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        путь = request.url.path
        тело = json.loads(request.content) if request.content else {}
        self.запросы.append((request.method, str(request.url), тело))

        for кусок, код in self.отказ_на.items():
            if кусок in путь:
                return httpx.Response(код, text="нет")

        if путь.endswith("/out.png"):
            return httpx.Response(200, content=КАДР,
                                  headers={"content-type": "image/png"})
        if путь.endswith("/status"):
            статус = self.статусы.pop(0) if len(self.статусы) > 1 else self.статусы[0]
            return httpx.Response(200, json={"status": статус})
        if "/requests/" in путь:
            return httpx.Response(200, json=self.ответ)
        return httpx.Response(200, json={"request_id": "req-42"})


@pytest.fixture(autouse=True)
def ключ(monkeypatch):
    monkeypatch.setattr(settings, "fal_api_key", "fal_test_key", raising=False)
    monkeypatch.setattr(settings, "fal_poll_interval", 0.0, raising=False)


@pytest.fixture
def подделка(monkeypatch):
    def поставить(сервер: Поддельный):
        транспорт = httpx.MockTransport(сервер)
        исходный = httpx.AsyncClient

        def подменённый(*args, **kwargs):
            kwargs["transport"] = транспорт
            return исходный(*args, **kwargs)

        monkeypatch.setattr("app.services.generation.providers.fal.httpx.AsyncClient",
                            подменённый)
        return сервер
    return поставить


def просьба(*, со_снимком=False) -> GenerationRequest:
    if со_снимком:
        return GenerationRequest(operation=Operation.IMAGE_TO_IMAGE,
                                 prompt="акварельный маяк",
                                 image=b"face-bytes", image_mime="image/jpeg")
    return GenerationRequest(operation=Operation.TEXT_TO_IMAGE, prompt="акварельный маяк")


# ─── ключ ────────────────────────────────────────────────────────────────────


async def test_without_a_key_the_provider_steps_aside(monkeypatch):
    """Пустой ключ — не отказ, а неучастие: реестр просто пройдёт мимо.

    Разница важная. Отказ считается провалом исполнителя и копится в причинах;
    неучастие означает, что строку завели заранее, а ключ ещё не выдали.
    """
    monkeypatch.setattr(settings, "fal_api_key", "", raising=False)
    assert FalProvider().available() is False


async def test_the_key_goes_in_falovn_own_scheme(подделка):
    """`Key`, а не `Bearer`. С `Bearer` fal отвечает 401 без объяснений."""
    сервер = подделка(Поддельный())
    await FalProvider().run(просьба(), model="fal-ai/nano-banana")
    # httpx.MockTransport не отдаёт заголовки наружу, поэтому проверяем то, что
    # собирает сам адаптер.
    assert FalProvider()._headers["Authorization"] == "Key fal_test_key"


# ─── очередь ─────────────────────────────────────────────────────────────────


async def test_it_waits_until_the_queue_says_completed(подделка):
    """Пока «в очереди» и «в работе» — ждём, и только потом забираем."""
    сервер = подделка(Поддельный(статусы=["IN_QUEUE", "IN_PROGRESS", "COMPLETED"]))
    out = await FalProvider().run(просьба(), model="fal-ai/nano-banana")
    assert out.data == КАДР
    опросов = sum(1 for м, u, _ in сервер.запросы if u.endswith("/status"))
    assert опросов == 3, f"опрашивали {опросов} раз вместо трёх"


async def test_the_frame_is_downloaded_not_linked(подделка):
    """Ссылка fal живёт недолго — байты забираем сразу.

    Иначе в хранилище легла бы не работа, а указатель на чужое, который завтра
    протухнет.
    """
    сервер = подделка(Поддельный())
    out = await FalProvider().run(просьба(), model="fal-ai/nano-banana")
    assert out.data == КАДР and out.mime == "image/png"
    assert any(u.endswith("/out.png") for _, u, _ in сервер.запросы), "кадр не скачали"


# ─── снимки ──────────────────────────────────────────────────────────────────


async def test_references_travel_as_data_uris(подделка):
    """Лицо человека не должно быть доступно по ссылке — только байтами внутри.

    fal принимает и публичный адрес, и base64. Первое нам запрещено: снимки
    лежат в закрытом хранилище, и ссылка на них не должна существовать вовсе.
    """
    сервер = подделка(Поддельный())
    await FalProvider().run(просьба(со_снимком=True), model="fal-ai/nano-banana/edit")
    _, _, тело = сервер.запросы[0]
    ссылки = тело["image_urls"]
    assert len(ссылки) == 1
    assert ссылки[0].startswith("data:image/jpeg;base64,")
    assert base64.b64decode(ссылки[0].split(",", 1)[1]) == b"face-bytes"


async def test_a_single_image_model_gets_one_reference(подделка):
    """У моделей с одиночным полем массив не примут — шлём первый снимок."""
    сервер = подделка(Поддельный())
    req = просьба(со_снимком=True)
    req.extra_images = [(b"second", "image/png")]
    from app.services.generation.providers import fal as модуль
    модуль._REFERENCE_FIELD["fal-ai/single-image"] = модуль._ONE
    await FalProvider().run(req, model="fal-ai/single-image")
    _, _, тело = сервер.запросы[0]
    assert isinstance(тело["image_url"], str)
    assert "image_urls" not in тело


# ─── отказы ──────────────────────────────────────────────────────────────────


async def test_a_refusal_is_how_the_next_provider_gets_its_turn(подделка):
    """Отказ обязан быть `GenerationUnavailable`, иначе реестр не пойдёт дальше.

    Любое другое исключение поднимется выше и оборвёт запрос целиком — вместо
    того чтобы отдать очередь запасному.
    """
    подделка(Поддельный(отказ_на={"nano-banana": 500}))
    with pytest.raises(GenerationUnavailable):
        await FalProvider().run(просьба(), model="fal-ai/nano-banana")


async def test_an_unknown_status_is_not_waited_on_forever(подделка):
    """Неизвестное состояние означает, что ждать можно вечно. Сдаёмся сразу."""
    подделка(Поддельный(статусы=["ERROR"]))
    with pytest.raises(GenerationUnavailable):
        await FalProvider().run(просьба(), model="fal-ai/nano-banana")


async def test_an_answer_without_a_picture_is_a_refusal(подделка):
    """Ответ без кадра — не «пустой результат», а отказ.

    Иначе в хранилище уехали бы нулевые байты, работа отметилась бы удачной, и
    человек заплатил бы за пустоту.
    """
    подделка(Поддельный(ответ={"images": []}))
    with pytest.raises(GenerationUnavailable):
        await FalProvider().run(просьба(), model="fal-ai/nano-banana")


async def test_it_gives_up_when_the_queue_never_finishes(подделка, monkeypatch):
    """Вечно «в работе» — тоже отказ: держать человека бесконечно нельзя."""
    monkeypatch.setattr(settings, "fal_request_timeout", 0.05, raising=False)
    подделка(Поддельный(статусы=["IN_PROGRESS"]))
    with pytest.raises(GenerationUnavailable):
        await FalProvider().run(просьба(), model="fal-ai/nano-banana")


# ─── то, что нашлось только на живом fal ─────────────────────────────────────


async def test_the_polling_urls_come_from_fal_not_from_us(подделка):
    """Адреса опроса берём у fal, а не собираем сами.

    Найдено живым запросом: у модели с под-путём (`fal-ai/nano-banana/edit`)
    очередь живёт по имени ПРИЛОЖЕНИЯ — `fal-ai/nano-banana/requests/…`, без
    `/edit`. Собранный «в лоб» адрес отвечает 405, и текст-в-кадр это скрывает:
    у него идентификатор из двух сегментов, и адрес совпадает случайно.
    """
    сервер = Поддельный()
    сервер.ответ = {"images": [{"url": "https://v3.fal.media/files/x/out.png",
                                "content_type": "image/png"}]}

    def с_адресами(request: httpx.Request) -> httpx.Response:
        путь = request.url.path
        сервер.запросы.append((request.method, str(request.url), {}))
        if путь.endswith("/out.png"):
            return httpx.Response(200, content=КАДР, headers={"content-type": "image/png"})
        if путь.endswith("/status"):
            return httpx.Response(200, json={"status": "COMPLETED"})
        if "/requests/" in путь:
            return httpx.Response(200, json=сервер.ответ)
        # Постановка отдаёт адреса БЕЗ под-пути — как настоящий fal.
        корень = "https://queue.fal.run/fal-ai/nano-banana/requests/req-42"
        return httpx.Response(200, json={
            "request_id": "req-42",
            "status_url": f"{корень}/status",
            "response_url": корень,
        })

    подделка(сервер)
    import app.services.generation.providers.fal as модуль
    исходный = httpx.AsyncClient
    транспорт = httpx.MockTransport(с_адресами)
    модуль.httpx.AsyncClient = lambda *a, **k: исходный(*a, **{**k, "transport": транспорт})
    try:
        await FalProvider().run(просьба(со_снимком=True), model="fal-ai/nano-banana/edit")
    finally:
        модуль.httpx.AsyncClient = исходный

    опросы = [u for _, u, _ in сервер.запросы if u.endswith("/status")]
    assert опросы, "статус не спрашивали"
    assert "/edit/requests/" not in опросы[0], (
        f"адрес опроса собран сами, а не взят у fal: {опросы[0]}"
    )


@pytest.mark.parametrize("со_снимком", [False, True])
async def test_the_frame_shape_is_always_named(подделка, со_снимком: bool):
    """Форма кадра называется всегда — и при рисовании, и при правке.

    Здесь стоял обратный тест: «при правке пропорции не шлём». Обоснование было
    ложным. Первый замер дал 0 удач из 4 с `aspect_ratio` и 2 из 4 без него, и я
    записал в виновные пропорции — а рядом стоял настоящий виновник, запрет про
    водяной знак. После его смягчения: 6/6 и с пропорциями, и без.

    Документация подтверждает: `9:16` — законное значение перечисления. А раз
    выбор свободен, он совпадает с OpenRouter: одна карточка каталога обязана
    давать кадр одной формы, кто бы её ни нарисовал.
    """
    сервер = подделка(Поддельный())
    модель = "fal-ai/nano-banana/edit" if со_снимком else "fal-ai/nano-banana"
    await FalProvider().run(просьба(со_снимком=со_снимком), model=модель)
    _, _, тело = сервер.запросы[0]
    assert тело.get("aspect_ratio"), "форма кадра не названа"


async def test_the_watermark_guard_is_softened(подделка):
    """«NO watermark» на чужой фотографии читается как «сними водяной знак».

    Это прямо запрещено правилами Google, и fal отвергал такой промпт: шесть
    попыток из шести. Мягкая формулировка — шесть из шести в другую сторону.

    Подменяется здесь, а не в общем тексте: тот проверен на OpenRouter, и менять
    его ради чужой цензуры нельзя без такого же замера.
    """
    from app.services.generation.providers import fal as модуль

    сервер = подделка(Поддельный())
    await FalProvider().run(просьба(со_снимком=True), model="fal-ai/nano-banana/edit")
    _, _, тело = сервер.запросы[0]
    assert модуль._WATERMARK_GUARD not in тело["prompt"], "крикливый запрет уехал к fal"
    assert модуль._SOFTER in тело["prompt"], "мягкая формулировка не подставилась"


# ─── отличия от OpenRouter, найденные по документации ────────────────────────


async def test_the_price_is_absent_and_that_is_known(подделка):
    """У работ fal цена пустая — и это записано, а не забыто.

    fal не отдаёт стоимость в ответе вовсе: там только `images` и
    `description`, а в статусе `metrics.inference_time`. Цена живёт в отдельном
    Usage API платформы. OpenRouter кладёт `usage.cost` прямо в ответ — значит
    переход на fal ЛОМАЕТ колонку `provider_cost_usd`, ради которой в базе всё
    и заводилось.

    Тест держит это утверждение явным. Если однажды fal начнёт присылать цену,
    он покраснеет — и это правильный повод её забрать.
    """
    подделка(Поддельный())
    out = await FalProvider().run(просьба(), model="fal-ai/nano-banana")
    assert out.cost_usd is None


async def test_a_price_is_never_guessed_from_a_pricelist(подделка):
    """И подставлять оценку вместо факта нельзя.

    Поле называется «что кадр стоил», а не «что он примерно стоил». Догадка,
    записанная как факт, хуже отсутствия числа: по ней начнут считать
    экономику, не зная, что считают прайс, а не расход.
    """
    сервер = Поддельный()
    сервер.ответ = {"images": [{"url": "https://v3.fal.media/files/x/out.png",
                                "content_type": "image/png"}],
                    "metrics": {"inference_time": 6.5}}
    подделка(сервер)
    out = await FalProvider().run(просьба(), model="fal-ai/nano-banana")
    assert out.cost_usd is None, "цена подставлена из прайса, а не взята фактом"
