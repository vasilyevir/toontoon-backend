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


async def test_fal_is_told_not_to_keep_our_requests():
    """Лица не должны месяц лежать в чужой истории.

    По умолчанию fal хранит тела запросов и ответов ТРИДЦАТЬ ДНЕЙ, а в теле у
    нас лицо человека, уехавшее base64. Без этого заголовка мы своими руками
    отдаём на месяц то, ради чего здесь сделано всё остальное: закрытый бакет,
    отдача через проверку владельца, срезание координат из EXIF, срок хранения,
    стирание при удалении аккаунта.

    Обещание «ваше лицо не разлетается» стоит ровно столько, сколько стоит
    самое слабое звено.
    """
    h = FalProvider()._headers
    assert h.get("X-Fal-Store-IO") == "0", "fal просят хранить наши запросы"


async def test_the_frame_on_their_cdn_expires_quickly():
    """Кадр забираем сразу — ссылка нужна на время скачивания, не дольше."""
    import json as _json

    h = FalProvider()._headers
    сырое = h.get("X-Fal-Object-Lifecycle-Preference")
    assert сырое, "срок жизни кадра на их CDN не назван"
    срок = _json.loads(сырое)["expiration_duration_seconds"]
    assert 0 < срок <= 3600, f"кадр живёт у них {срок} с — слишком долго"


# ─── кадр приходит в ответе, а не ссылкой ────────────────────────────────────
#
# Хранилище fal оказалось единственным ненадёжным звеном пути. Заказ, ожидание
# и результат отвечали за доли секунды, а `v3b.fal.media` отдавал первые
# пятнадцать килобайт и замолкал — ровно начальное окно TCP. Одинаково на
# HTTP/2 и HTTP/1.1, на IPv4 и IPv6: путь мёртв, протокол ни при чём.
#
# `sync_mode` убирает этот шаг совсем. Мы всё равно скачивали кадр немедленно,
# держать его у них было незачем — а заодно готовое лицо перестало лежать на
# чужом CDN даже по недолгой ссылке.

async def test_просим_кадр_прямо_в_ответе(подделка):
    """Без этого флага fal кладёт кадр на свой CDN и присылает адрес."""
    сервер = подделка(Поддельный())
    await FalProvider().run(просьба())
    _, _, тело = сервер.запросы[0]
    assert тело.get("sync_mode") is True


async def test_кадр_из_ответа_разбирается_без_сети(подделка):
    """`data:` в поле `url` — это сам кадр. Ходить за ним никуда не надо."""
    картинка = b"\x89PNG\r\n\x1a\n" + b"inline-bytes"
    сервер = подделка(Поддельный(ответ={"images": [{
        "url": "data:image/png;base64," + base64.b64encode(картинка).decode(),
        "content_type": "image/png"}]}))

    out = await FalProvider().run(просьба())

    assert out.data == картинка
    assert out.mime == "image/png"
    # Ни одного запроса к хранилищу: заказ, статус, результат — и всё.
    адреса = [url for _, url, _ in сервер.запросы]
    assert not [a for a in адреса if "fal.media" in a], f"полезли в хранилище: {адреса}"


async def test_ссылка_всё_ещё_работает(подделка):
    """Модель вправе не знать про `sync_mode` — тогда придёт адрес."""
    сервер = подделка(Поддельный())
    out = await FalProvider().run(просьба())
    assert out.data == КАДР
    assert [a for a in (u for _, u, _ in сервер.запросы) if "fal.media" in a]


async def test_пустой_кадр_в_ответе_это_отказ(подделка):
    """`data:image/png;base64,` без тела — не картинка, а беда."""
    подделка(Поддельный(ответ={"images": [{"url": "data:image/png;base64,"}]}))
    with pytest.raises(GenerationUnavailable):
        await FalProvider().run(просьба())


async def test_битая_строка_не_роняет_адаптер(подделка):
    """Не base64 — отказ по-человечески, а не исключение из недр библиотеки."""
    подделка(Поддельный(ответ={"images": [{"url": "data:image/png;base64,%%%не-base64%%%"}]}))
    with pytest.raises(GenerationUnavailable):
        await FalProvider().run(просьба())


# ─── диалект gpt-image-2 ─────────────────────────────────────────────────────
#
# У fal один транспорт на все модели, а вход у каждой свой. nano-banana берёт
# `aspect_ratio: "9:16"`; у gpt-image-2 такого поля нет вовсе — есть `image_size`
# с пресетами и `quality`, где умолчание `high` вчетверо дороже `medium`.
# Отправить ему тело от nano-banana значит либо получить отказ, либо заплатить
# за `high`, не заметив.

def _просьба_с_формой(aspect: str) -> GenerationRequest:
    return GenerationRequest(operation=Operation.IMAGE_TO_IMAGE, prompt="маяк",
                             image=b"face-bytes", image_mime="image/jpeg",
                             params={"aspect": aspect})


async def test_gpt_image_2_говорит_на_своём(подделка):
    сервер = подделка(Поддельный())
    await FalProvider().run(_просьба_с_формой("9:16"), model="openai/gpt-image-2/edit")
    _, _, тело = сервер.запросы[0]
    assert "aspect_ratio" not in тело, "у gpt-image-2 нет такого поля — отправлять нельзя"
    # Не пресет, а числа OpenRouter: пресет давал 608×1088, на четверть мельче.
    assert тело["image_size"] == {"width": 864, "height": 1536}
    assert тело["quality"] == "medium", "умолчание fal — high, самый дорогой режим"
    assert тело["sync_mode"] is True
    assert тело["image_urls"] and тело["image_urls"][0].startswith("data:image/jpeg;base64,")


async def test_незнакомая_форма_уходит_как_auto(подделка):
    сервер = подделка(Поддельный())
    await FalProvider().run(_просьба_с_формой("7:5"), model="openai/gpt-image-2/edit")
    assert сервер.запросы[0][2]["image_size"] == "auto"


async def test_nano_banana_не_задет_диалектом(подделка):
    """Поведение по умолчанию — прежнее: все старые тесты писались под него."""
    сервер = подделка(Поддельный())
    await FalProvider().run(_просьба_с_формой("9:16"), model="fal-ai/nano-banana/edit")
    _, _, тело = сервер.запросы[0]
    assert тело["aspect_ratio"] == "9:16"
    assert "quality" not in тело and "image_size" not in тело


async def test_разрешение_пробрасывается_nano_pro(подделка):
    сервер = подделка(Поддельный())
    req = GenerationRequest(operation=Operation.IMAGE_TO_IMAGE, prompt="маяк",
                            image=b"f", image_mime="image/jpeg",
                            params={"aspect": "9:16", "resolution": "2K"})
    await FalProvider().run(req, model="fal-ai/nano-banana-pro/edit")
    тело = сервер.запросы[0][2]
    assert тело["resolution"] == "2K" and тело["aspect_ratio"] == "9:16"


async def test_явный_размер_и_качество_у_gpt_image_2(подделка):
    сервер = подделка(Поддельный())
    req = GenerationRequest(operation=Operation.IMAGE_TO_IMAGE, prompt="маяк",
                            image=b"f", image_mime="image/jpeg",
                            params={"aspect": "9:16", "quality": "high",
                                    "size": {"width": 1152, "height": 1536}})
    await FalProvider().run(req, model="openai/gpt-image-2/edit")
    тело = сервер.запросы[0][2]
    assert тело["quality"] == "high", "явное качество сильнее умолчания medium"
    assert тело["image_size"] == {"width": 1152, "height": 1536}, "явный размер сильнее пресета"
    assert "aspect_ratio" not in тело


async def test_без_параметров_nano_pro_как_обычный(подделка):
    """Ничего не просили — ничего лишнего не ушло: fal сам поставит 1K."""
    сервер = подделка(Поддельный())
    await FalProvider().run(_просьба_с_формой("9:16"), model="fal-ai/nano-banana-pro/edit")
    тело = сервер.запросы[0][2]
    assert "resolution" not in тело and тело["aspect_ratio"] == "9:16"


@pytest.mark.parametrize("url", [
    "http://v3b.fal.media/files/x/out.png",          # не https
    "https://127.0.0.1:8080/internal/secret.png",    # свой узел
    "https://evil.example/fal.media/out.png",        # похоже, но не то
    "https://notfal.media/out.png",                  # суффикс без точки
])
async def test_a_frame_link_outside_fal_storage_is_not_fetched(url, monkeypatch):
    """Ссылку на кадр присылает fal, но верить ей как своей нельзя: подмена
    ответа или `FAL_BASE_URL` превращала докачку в запрос к любому адресу,
    до которого дотягивается сервер. Отказ — до первого соединения."""
    async def не_ходить(*a, **k):
        raise AssertionError("соединение не должно открываться")
    monkeypatch.setattr(httpx.AsyncClient, "get", не_ходить)
    with pytest.raises(GenerationUnavailable):
        await FalProvider()._download_in_pieces(url)


async def test_polling_urls_from_a_foreign_host_are_replaced_by_our_own():
    """В адреса опроса уходит ключ. Если fal (или тот, кто за него) прислал
    чужой узел, берём свои собранные адреса — они всегда на `fal_base_url`."""
    from app.services.generation.providers import fal as модуль
    наш = httpx.URL(settings.fal_base_url).host
    assert модуль._is_fal_host("v3b.fal.media") and модуль._is_fal_host("fal.media")
    assert not модуль._is_fal_host("evil.example") and not модуль._is_fal_host(None)

    class Ответ:
        status_code = 200
        text = ""
        def json(self):
            return {"request_id": "r1",
                    "status_url": "https://attacker.example/status",
                    "response_url": f"https://{наш}/fal-ai/nano-banana/requests/r1"}
    class Клиент:
        async def post(self, *a, **k):
            return Ответ()
    _, статус, ответ = await FalProvider()._submit(Клиент(), "fal-ai/nano-banana", {})
    assert httpx.URL(статус).host == наш
    assert httpx.URL(ответ).host == наш


# ─── отказ по содержанию — окончательный ─────────────────────────────────────
#
# 422 у fal бывает двух сортов. «Поле не то» — наша ошибка, и её правильно
# обойти другим исполнителем. «Модель не взялась за снимок» — решение, и
# обходить его значит идти к тому, кто не проверяет. Проверено живьём: OpenAI
# отклонил «сделай меня как Том Круз» за пять секунд, а nano-banana нарисовал.

ОТКАЗ_FAL = ('{"detail":[{"loc":["body","prompt"],"msg":"The content could not be '
             'processed because it contained material flagged by a content checker.",'
             '"type":"content_policy_violation"}]}')


async def test_422_по_содержанию_это_ContentRefused(подделка):
    from app.services.generation.operations import ContentRefused
    class Отказывающий(Поддельный):
        def __call__(self, request):
            if "/requests/" in request.url.path and not request.url.path.endswith("/status"):
                return httpx.Response(422, text=ОТКАЗ_FAL)
            return super().__call__(request)
    подделка(Отказывающий())
    with pytest.raises(ContentRefused):
        await FalProvider().run(просьба(со_снимком=True), model="openai/gpt-image-2/edit")


async def test_обычная_422_остаётся_обходимой(подделка):
    """«Поле не то» — не отказ по содержанию: цепочка вправе пробовать дальше."""
    from app.services.generation.operations import ContentRefused
    class Кривой(Поддельный):
        def __call__(self, request):
            if "/requests/" in request.url.path and not request.url.path.endswith("/status"):
                return httpx.Response(422, text='{"detail":"image_size: unexpected value"}')
            return super().__call__(request)
    подделка(Кривой())
    with pytest.raises(GenerationUnavailable) as e:
        await FalProvider().run(просьба(со_снимком=True), model="openai/gpt-image-2/edit")
    assert not isinstance(e.value, ContentRefused)
