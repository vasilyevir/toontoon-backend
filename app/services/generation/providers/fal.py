"""fal.ai — исполнитель через очередь.

Витрина, как и OpenRouter: один ключ и один адрес открывают семейства моделей
разных вендоров. Поэтому адаптер здесь один, а какая за строкой реестра стоит
модель — вопрос данных, а не релиза (CH-21).

**Очередь, а не синхронный вызов.** У fal есть и `fal.run`, который держит
соединение до конца работы. Мы ходим через `queue.fal.run`: кадр рисуется от
двадцати секунд до минуты, и висящее всё это время соединение — лишняя точка
отказа, которую тут уже проходили с видео. Очередь отвечает сразу, а готовность
спрашивается отдельно; ровно так же устроен адаптер kie.ai.

**Снимки уезжают как data URI.** fal принимает и публичную ссылку, и base64
прямо в поле. Ссылку дать нельзя: это лица людей, и они лежат в закрытом
хранилище — ссылка на них не должна существовать вовсе. base64 дороже по
трафику, и это осознанная цена.

Что откуда:
* submit  — POST {base}/{model}         → {"request_id": …}
* статус  — GET  {base}/{model}/requests/{id}/status  → IN_QUEUE | IN_PROGRESS | COMPLETED
* ответ   — GET  {base}/{model}/requests/{id}         → {"images": [{"url", "content_type"}]}

Документация: https://docs.fal.ai/model-apis/model-endpoints/queue
"""
from __future__ import annotations

import asyncio
import base64
import logging
import time
from typing import Optional

import httpx

from app.config import settings
from app.services import prompt_style
from app.services.generation.operations import (
    GenerationRequest,
    GenerationResult,
    GenerationUnavailable,
    Operation,
)
from app.services.generation.providers.base import Provider

logger = logging.getLogger("toontoon.generation.fal")

# Как модель называет поле со снимками.
#
# У fal единого имени нет: редактирующие модели семейства Nano Banana берут
# массив `image_urls`, а часть однокадровых — одиночный `image_url`. Знание это
# про модель, а не про адаптер, и когда список перерастёт десяток строк, ему
# место в `limits` строки реестра — вместе с изменением контракта `Provider`,
# который сейчас про эти limits ничего не знает.
#
# Пока строк мало, держать их здесь честнее, чем менять контракт ради двух
# случаев.
_MANY = "image_urls"
_ONE = "image_url"
_REFERENCE_FIELD: dict[str, str] = {
    "fal-ai/nano-banana/edit": _MANY,
    "fal-ai/nano-banana-pro/edit": _MANY,
    "fal-ai/nano-banana-2/edit": _MANY,
    "google/nano-banana-lite/edit": _MANY,
}
# Умолчание — массив: так устроены все редактирующие модели, которые нам нужны
# сегодня, а одиночное поле встречается у вспомогательных.
_DEFAULT_REFERENCE_FIELD = _MANY


# Наш запрет на надписи, который у fal читается как просьба о запрещённом.
#
# Строка «NO text NO letters NO words NO watermark in the image» отвергается
# проверкой содержимого — почти наверняка потому, что «no watermark» на чужой
# фотографии читается как «сними водяной знак», а это прямо запрещено правилами
# Google. Замер на живом fal, шесть попыток на вариант:
#
#     просьба без приписки                          6/6
#     приписка без этого пункта                     6/6
#     ТОЛЬКО этот пункт                             0/6
#     он же, сказанный мягко                        6/6
#
# Ноль из шести — это не невезение, а правило.
#
# Подменяем здесь, а не в общем тексте: тот проверен на OpenRouter, им сделан
# весь нынешний каталог, и менять его ради чужой цензуры нельзя без такого же
# замера на подавление надписей. Вопрос вынесен наружу — формулировка и правда
# крикливая, и мягкая, возможно, лучше везде.
_WATERMARK_GUARD = "NO text NO letters NO words NO watermark in the image"
_SOFTER = "no visible text or logos"


def _softened(guards: str) -> str:
    return guards.replace(_WATERMARK_GUARD, _SOFTER)


class FalProvider(Provider):
    """Один адаптер — сколько угодно строк реестра.

    Инстансы отличаются только идентификатором: `fal`, `fal_nano`, `fal_flux`.
    Модель приходит из строки, всё остальное общее.
    """

    def __init__(self, provider_id: str = "fal") -> None:
        self.id = provider_id

    @property
    def operations(self) -> frozenset[Operation]:
        # Реставрацию сюда не пускаем, и это не забывчивость. Она обязана
        # сохранить кадр целиком и не имеет права ничего дорисовывать; какая из
        # моделей fal так умеет — вопрос замера, а не догадки. До замера пусть
        # реставрацией занимается тот, на ком она проверена.
        return frozenset({Operation.TEXT_TO_IMAGE, Operation.IMAGE_TO_IMAGE})

    @property
    def model(self) -> str:
        return settings.fal_image_model

    def available(self) -> bool:
        return bool(settings.fal_api_key.strip())

    async def run(
        self, request: GenerationRequest, *, model: Optional[str] = None
    ) -> GenerationResult:
        model = model or self.model
        editing = request.operation is Operation.IMAGE_TO_IMAGE

        # Запреты дописываются в положительный промпт — как у OpenRouter и по
        # той же причине: отдельного негатива у этих моделей нет, а наборы для
        # фотографии и для рисунка разные.
        prompt = request.prompt or ""
        body: dict = {
            "prompt": f"{prompt}, {_softened(prompt_style.guards_for(editing=editing))}",
            "num_images": 1,
            "output_format": settings.fal_output_format,
        }

        # Пропорции назначаем ТОЛЬКО когда рисуем с нуля.
        #
        # При правке кадра форма берётся из самого снимка — и это не уступка
        # чужому API, а то, как правильно: человек прислал свою фотографию, и
        # обрезать её под нашу вертикаль значит испортить то, за чем он пришёл.
        #
        # Заодно это лечит отказы. Замер на живом fal, четыре попытки на
        # вариант: с `aspect_ratio` прошло 0 из 4, без него — 3–4 из 4. Модель
        # отвечала то «не смогла построить кадр», то нарушением правил, и
        # связать это с пропорциями по тексту ошибки было нельзя.
        if not request.references:
            aspect = request.params.get("aspect") or settings.fal_aspect_ratio
            if aspect:
                body["aspect_ratio"] = aspect

        if request.references:
            field = _REFERENCE_FIELD.get(model, _DEFAULT_REFERENCE_FIELD)
            uris = [
                f"data:{mime};base64," + base64.b64encode(data).decode("ascii")
                for data, mime in request.references
            ]
            # Одиночное поле берёт первый снимок: остальные приложить некуда, и
            # молча уронить их лучше, чем отправить запрос, который модель не
            # поймёт. Сколько снимков берёт строка — объявлено в её `limits`, и
            # реестр отсеивает лишнее раньше нас.
            body[field] = uris if field == _MANY else uris[0]

        payload = await self._through_the_queue(model, body)
        data, mime = await self._first_image(payload)
        return GenerationResult(
            data=data,
            mime=mime,
            provider_id=self.id,
            model=model,
        )

    # ─── Транспорт ───────────────────────────────────────────────────────────

    @property
    def _headers(self) -> dict:
        # Именно `Key`, а не `Bearer`: у fal своя схема, и с `Bearer` приходит
        # 401 без объяснений.
        return {
            "Authorization": f"Key {settings.fal_api_key}",
            "Content-Type": "application/json",
        }

    def _base(self, model: str) -> str:
        return f"{settings.fal_base_url.rstrip('/')}/{model.strip('/')}"

    async def _through_the_queue(self, model: str, body: dict) -> dict:
        """Поставить в очередь, дождаться, забрать. Отказ — повод уйти дальше."""
        deadline = time.monotonic() + settings.fal_request_timeout
        async with httpx.AsyncClient(timeout=settings.fal_http_timeout) as client:
            request_id, status_url, response_url = await self._submit(client, model, body)
            logger.info("fal: заказ %s поставлен в очередь моделью %s", request_id, model)

            while True:
                if time.monotonic() > deadline:
                    raise GenerationUnavailable(
                        f"fal: {model} не уложился в {settings.fal_request_timeout:.0f} с"
                    )
                status = await self._status(client, status_url)
                if status == "COMPLETED":
                    break
                # IN_QUEUE и IN_PROGRESS ждём одинаково: разница между ними для
                # нас никакая, ждать всё равно надо.
                await asyncio.sleep(settings.fal_poll_interval)

            return await self._result(client, response_url)

    def _fallback_urls(self, model: str, request_id: str) -> tuple[str, str]:
        """Адреса опроса, если fal их не прислал.

        Только первые два сегмента модели: у fal идентификатор бывает с
        под-путём (`fal-ai/nano-banana/edit`), а очередь живёт по имени
        ПРИЛОЖЕНИЯ — `fal-ai/nano-banana/requests/…`, без `/edit`. Собранный
        «в лоб» адрес отвечает 405, и текст-в-кадр это скрывает: у него
        идентификатор из двух сегментов, и адрес совпадает случайно.
        """
        части = model.strip("/").split("/")
        приложение = "/".join(части[:2]) if len(части) > 2 else model.strip("/")
        корень = f"{settings.fal_base_url.rstrip('/')}/{приложение}/requests/{request_id}"
        return f"{корень}/status", корень

    async def _submit(
        self, client: httpx.AsyncClient, model: str, body: dict
    ) -> tuple[str, str, str]:
        """Поставить в очередь и взять адреса опроса — те, что дал сам fal.

        Он присылает `status_url` и `response_url` готовыми, и брать их у него
        надёжнее, чем собирать самому: правило сборки мы однажды угадали
        неверно и получили 405 на всех моделях с под-путём. Своя сборка
        осталась запасной на случай, если полей не будет.
        """
        resp = await client.post(self._base(model), json=body, headers=self._headers)
        if resp.status_code >= 300:
            raise GenerationUnavailable(f"fal HTTP {resp.status_code}: {resp.text[:300]}")
        try:
            payload = resp.json()
            request_id = str(payload["request_id"])
        except (ValueError, KeyError, TypeError) as exc:
            raise GenerationUnavailable(
                f"fal не вернул request_id: {resp.text[:200]}") from exc

        свой_статус, свой_ответ = self._fallback_urls(model, request_id)
        return (
            request_id,
            str(payload.get("status_url") or свой_статус),
            str(payload.get("response_url") or свой_ответ),
        )

    async def _status(self, client: httpx.AsyncClient, status_url: str) -> str:
        resp = await client.get(status_url, headers=self._headers)
        if resp.status_code >= 300:
            raise GenerationUnavailable(
                f"fal: статус недоступен, HTTP {resp.status_code}: {resp.text[:200]}")
        payload = resp.json() if resp.content else {}
        status = str(payload.get("status") or "").upper()
        # Всё, что не «в очереди», не «в работе» и не «готово», — отказ. Молчать
        # тут нельзя: неизвестное состояние означает, что ждать можно вечно.
        if status not in {"IN_QUEUE", "IN_PROGRESS", "COMPLETED"}:
            raise GenerationUnavailable(f"fal ответил состоянием {status or '(пусто)'}")
        return status

    async def _result(self, client: httpx.AsyncClient, response_url: str) -> dict:
        resp = await client.get(response_url, headers=self._headers)
        if resp.status_code >= 300:
            raise GenerationUnavailable(
                f"fal: результат недоступен, HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            return resp.json()
        except ValueError as exc:
            raise GenerationUnavailable("fal ответил не JSON") from exc

    async def _first_image(self, payload: dict) -> tuple[bytes, str]:
        """Забрать байты кадра по ссылке из ответа.

        fal отдаёт не сам кадр, а ссылку на него в своём хранилище — и ссылка
        живёт недолго. Скачиваем сразу: результат должен уехать к нам в
        хранилище, а не остаться указателем на чужое.
        """
        images = payload.get("images") or []
        if not images or not isinstance(images, list):
            raise GenerationUnavailable(f"fal вернул ответ без кадра: {str(payload)[:200]}")
        first = images[0] or {}
        url = first.get("url")
        if not url:
            raise GenerationUnavailable("fal вернул кадр без ссылки")

        async with httpx.AsyncClient(timeout=settings.fal_http_timeout) as client:
            resp = await client.get(url)
        if resp.status_code != 200 or not resp.content:
            raise GenerationUnavailable(
                f"fal: кадр не скачался, HTTP {resp.status_code}")
        mime = first.get("content_type") or resp.headers.get("content-type") or "image/png"
        return resp.content, str(mime).split(";", 1)[0].strip()
