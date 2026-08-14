"""Kling — единственный подключённый инструмент с ручкой «сохрани лицо».

Остальные адаптеры умеют нарисовать картинку по тексту, а фотографию человека
используют в лучшем случае как вдохновение. Kling принимает снимок как
референс с явным указанием, что именно с него взять — `image_reference=face` —
и насколько строго это соблюдать (`human_fidelity`). Ровно это обещает продукт
фразой «твоё фото в этом стиле», и ровно этого до сих пор не было ни у одного
провайдера в реестре (CH-19).

Две особенности против остальных адаптеров:

* **Две схемы авторизации.** У Kling сосуществуют две консоли: новая выдаёт
  один ключ, который идёт в заголовок как есть, старая — пару access/secret,
  из которой JWT собирается на нашей стороне. Поддержаны обе, потому что по
  документации не угадать, какая досталась аккаунту.
* **Асинхронность.** Ответ приходит не на запрос, а задачей: сначала task_id,
  потом опрос до готовности. Опрос ограничен по времени, и по исчерпании
  бюджета адаптер честно отказывается — реестр уводит запрос к следующему
  провайдеру, а кошелёк возвращает TOONTOON.

Снимок уходит в теле запроса base64, не ссылкой. Это то же правило, что и у
остальных: ничто о лице человека не должно быть доступно по URL.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import time

from typing import Optional

import httpx
import jwt

from app.config import settings
from app.services.generation.operations import (
    GenerationRequest,
    GenerationResult,
    GenerationUnavailable,
    Operation,
)
from app.services.generation.providers.base import Provider

logger = logging.getLogger("toontoon.generation.kling")

_CREATE_PATH = "/v1/images/generations"
# Срок жизни токена с запасом: подписываем на полчаса, но собираем заново на
# каждый запрос — кэшировать нечего, подпись стоит микросекунды.
_TOKEN_TTL_SECONDS = 1800
# Небольшой сдвиг назад: часы у нас и у них разъезжаются на секунды, и без
# этого свежий токен иногда отвергается как «ещё не действующий».
_CLOCK_SKEW_SECONDS = 5


def _auth_token() -> str:
    """Токен для заголовка — по той схеме, ключи которой заполнены.

    Новая консоль Kling («API Platform») выдаёт один ключ, и он идёт как есть.
    Старая («Open Platform») выдаёт пару, и тогда мы сами подписываем JWT.
    Прямой ключ проверяется первым: если аккаунт новый, пары у него просто нет.
    """
    if settings.kling_api_key:
        return settings.kling_api_key

    now = int(time.time())
    return jwt.encode(
        {"iss": settings.kling_access_key, "exp": now + _TOKEN_TTL_SECONDS,
         "nbf": now - _CLOCK_SKEW_SECONDS},
        settings.kling_secret_key,
        algorithm="HS256",
        headers={"alg": "HS256", "typ": "JWT"},
    )


class KlingProvider(Provider):
    """Одна реализация — несколько строк в реестре.

    Поколения Kling различаются способом приложить фотографию, поэтому под
    каждое заводится своя строка со своим id и своей моделью: `kling` с
    третьей версией на основном потоке с референсами и `kling_v2` со второй
    там, где нужна явная ручка на лицо. Инстансы отличаются только id — всё
    остальное решает модель из строки.
    """

    def __init__(self, provider_id: str = "kling") -> None:
        self.id = provider_id

    @property
    def operations(self) -> frozenset[Operation]:
        return frozenset({Operation.TEXT_TO_IMAGE, Operation.IMAGE_TO_IMAGE})

    @property
    def model(self) -> str:
        return settings.kling_image_model

    def available(self) -> bool:
        return bool(
            settings.kling_api_key
            or (settings.kling_access_key and settings.kling_secret_key)
        )

    async def run(
        self, request: GenerationRequest, *, model: Optional[str] = None
    ) -> GenerationResult:
        model = model or self.model
        task_id = await self._create(request, model)
        url = await self._await_result(task_id)
        return GenerationResult(
            data=await self._download(url), mime="image/png",
            provider_id=self.id, model=model,
        )

    # ─── Шаги ────────────────────────────────────────────────────────────────

    async def _create(self, request: GenerationRequest, model: str) -> str:
        body: dict = {
            "model_name": model,
            # У Kling жёсткий предел в 2500 символов — наш собранный промпт
            # занимает около 1200, но обрезка здесь дешевле, чем отказ на той
            # стороне из-за случайно длинного пользовательского текста.
            "prompt": (request.prompt or "")[:2500],
            "n": 1,
            "aspect_ratio": settings.kling_aspect_ratio,
        }
        if request.negative_prompt:
            body["negative_prompt"] = request.negative_prompt[:2500]

        if request.image:
            self._attach_reference(body, request.image, model)

        data = await self._post(_CREATE_PATH, body)
        task_id = (data or {}).get("task_id")
        if not task_id:
            raise GenerationUnavailable(f"Kling не вернул task_id: {str(data)[:200]}")
        return task_id

    def _attach_reference(self, body: dict, image: bytes, model: str) -> None:
        """Приложить снимок и сказать, что с него взять.

        Поле одно на все поколения, включая третье: `image` плюс
        `image_reference=face` и `human_fidelity`. Проверено вживую на
        `kling-v3` — лицо, причёска, наряд и поза сохраняются, меняется
        только то, о чём просит промпт.

        Здесь была ветка с нумерованными `image_1`…`image_10` и ссылкой
        `@image_1` из промпта — так описывают третью версию сторонние
        обёртки. Официальный API принимает такой запрос с кодом 200 и
        **молча игнорирует** снимок: на выходе другой человек, а по ответу
        не отличить. Поэтому единственная проверка, которой здесь можно
        верить, — сравнение картинок глазами, и она уже сделана.
        """
        body["image"] = base64.b64encode(image).decode("ascii")
        body["image_reference"] = settings.kling_image_reference
        body["image_fidelity"] = settings.kling_image_fidelity
        body["human_fidelity"] = settings.kling_human_fidelity

    async def _await_result(self, task_id: str) -> str:
        deadline = asyncio.get_event_loop().time() + settings.kling_poll_timeout

        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(settings.kling_poll_interval)
            try:
                data = await self._get(f"{_CREATE_PATH}/{task_id}")
            except GenerationUnavailable:
                raise
            except Exception as exc:
                # Сетевая осечка посреди опроса — не повод хоронить задачу,
                # она продолжает считаться на той стороне.
                logger.warning("Kling: опрос %s сорвался (%r), повторяю", task_id, exc)
                continue

            status = str((data or {}).get("task_status") or "").lower()
            if status in {"succeed", "succeeded", "success"}:
                images = ((data.get("task_result") or {}).get("images")) or []
                url = images[0].get("url") if images else None
                if not url:
                    raise GenerationUnavailable("Kling отчитался об успехе без картинки")
                return url
            if status in {"failed", "fail", "error"}:
                reason = data.get("task_status_msg") or "без причины"
                raise GenerationUnavailable(f"Kling отказал: {reason}")

        raise GenerationUnavailable(
            f"Kling не уложился в {settings.kling_poll_timeout:.0f} с"
        )

    async def _download(self, url: str) -> bytes:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(url)
        if resp.status_code != 200 or not resp.content:
            raise GenerationUnavailable(f"Картинка Kling не забралась: HTTP {resp.status_code}")
        return resp.content

    # ─── Транспорт ───────────────────────────────────────────────────────────

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {_auth_token()}",
            "Content-Type": "application/json",
        }

    async def _post(self, path: str, body: dict) -> dict:
        async with httpx.AsyncClient(timeout=settings.kling_request_timeout) as client:
            resp = await client.post(
                f"{settings.kling_base_url.rstrip('/')}{path}",
                json=body, headers=self._headers(),
            )
        return self._unwrap(resp)

    async def _get(self, path: str) -> dict:
        async with httpx.AsyncClient(timeout=settings.kling_request_timeout) as client:
            resp = await client.get(
                f"{settings.kling_base_url.rstrip('/')}{path}", headers=self._headers()
            )
        return self._unwrap(resp)

    @staticmethod
    def _unwrap(resp: httpx.Response) -> dict:
        """Достать `data` из конверта Kling, любой сбой превратив в отказ.

        Отказ — это способ сказать реестру «бери следующего», поэтому здесь
        нет ни одного пути, который вернул бы пустоту молча.
        """
        if resp.status_code != 200:
            raise GenerationUnavailable(f"Kling HTTP {resp.status_code}: {resp.text[:300]}")
        try:
            payload = resp.json()
        except ValueError:
            raise GenerationUnavailable("Kling ответил не JSON")
        if payload.get("code") not in (0, None):
            raise GenerationUnavailable(f"Kling code={payload.get('code')}: {payload.get('message')}")
        return payload.get("data") or {}
