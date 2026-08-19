"""OpenRouter — один адаптер вместо восьми.

Остальные провайдеры в реестре — это вендоры: свой ключ, своя схема
авторизации, свой формат ответа. OpenRouter — витрина поверх вендоров: один
ключ открывает четыре десятка генераторов, и добавление ещё одного стоит
строки в `generation_providers`, а не нового файла здесь. Ровно то разделение,
на котором стоит реестр (CH-21): модель — данные, адаптер — код.

Практический смысл в сравнении. Чтобы выбрать модель, кандидатов надо прогнать
на одном промпте и одних лицах; пока каждый вендор требовал отдельного
аккаунта, сравнение упиралось не в качество, а в пять регистраций.

Три отличия от остальных адаптеров:

* **Идентификатор несёт модель.** В реестре живут строки `openrouter_gemini`,
  `openrouter_flux` и так далее, все с этим классом; какая за ними модель —
  написано в колонке `model`. Разбирать id на части не нужно, но и складывать
  все модели в одну строку нельзя: у них разные цены и разный приоритет.
* **Негатива нет.** В унифицированном API параметра под запреты не
  предусмотрено, поэтому запреты дописываются в положительный промпт — тем же
  способом и тем же текстом, что у OpenAI Images.
* **Цена приходит фактом.** Ответ содержит `usage.cost` в долларах за этот
  самый запрос. Это точнее прайса, умноженного на догадку о числе токенов, и
  именно это число попадает в результат.

Снимок уходит base64 в теле запроса. API принимает и ссылку, но ссылка на
лицо человека не должна существовать: она переживает и сессию, и удаление
аккаунта, а мы этого не обещали.

Отдельно стоит помнить: витрина — это ещё один обработчик в цепочке. Для
замеров на своих же лицах безразлично, для боевого потока с чужими
фотографиями это решение, которое принимают осознанно.
"""
from __future__ import annotations

import base64
import logging
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

logger = logging.getLogger("toontoon.generation.openrouter")

_IMAGES_PATH = "/images"
_MODELS_PATH = "/images/models"

# Что принимает каждая модель, спрошенное у витрины один раз за процесс.
# Наборы у них разные: Recraft берёт `aspect_ratio`, но не знает `resolution`,
# у Gemini есть обе, у кого-то нет ни одной. Слать всё подряд — это 400 вместо
# картинки, а зашивать список в код значит возвращать релиз туда, откуда его
# убрал реестр. Пустой словарь означает «спросить не удалось» — тогда шлём
# минимум, который принимают все.
_capabilities: dict[str, dict] = {}

# Куда повторять запрос, если модель отказалась делать кадр такого размера.
_NEXT_RESOLUTION = "2K"


def _is_too_small(message: str) -> bool:
    """Отличить «кадр слишком мал» от любого другого отказа с кодом 400."""
    lowered = message.lower()
    return "output pixels" in lowered and "requires at least" in lowered


async def _supported(model: str) -> Optional[dict]:
    """Какие параметры принимает модель. None — если выяснить не вышло."""
    if not _capabilities:
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.get(
                    f"{settings.openrouter_base_url.rstrip('/')}{_MODELS_PATH}"
                )
            for row in resp.json().get("data", []):
                _capabilities[row["id"]] = row.get("supported_parameters") or {}
        except Exception as exc:  # noqa: BLE001 — не повод срывать генерацию
            logger.warning("OpenRouter: не удалось прочитать список моделей (%r)", exc)
            return None
    return _capabilities.get(model)


class OpenRouterProvider(Provider):
    """Одна реализация — сколько угодно строк в реестре.

    Инстансы отличаются только id: `openrouter_gemini`, `openrouter_flux`,
    `openrouter_recraft`. Всё остальное — модель, а она приходит из строки.
    """

    def __init__(self, provider_id: str = "openrouter") -> None:
        self.id = provider_id

    @property
    def operations(self) -> frozenset[Operation]:
        return frozenset({
            Operation.TEXT_TO_IMAGE, Operation.IMAGE_TO_IMAGE, Operation.RESTORE,
        })

    @property
    def model(self) -> str:
        return settings.openrouter_image_model

    def available(self) -> bool:
        return bool(settings.openrouter_api_key)

    async def run(
        self, request: GenerationRequest, *, model: Optional[str] = None
    ) -> GenerationResult:
        model = model or self.model
        editing = request.operation is Operation.IMAGE_TO_IMAGE
        restoring = request.operation is Operation.RESTORE

        # Запреты дописываются в положительный промпт: негатива в этом API нет.
        # Наборы разные для фото и для рисунка — на живом лице инструкция
        # «дружелюбное нестрашное мультяшное лицо» работает против того, что
        # обещает экран.
        #
        # Реставрации они не дописываются вовсе: там нельзя ничего добавлять и
        # ничего приукрашивать, а любой набор запретов на стиль подталкивает
        # именно к этому. У неё свой единственный текст.
        prompt = request.prompt or (prompt_style.RESTORE_PROMPT if restoring else "")
        body: dict = {
            "model": model,
            "prompt": prompt if restoring
                      else f"{prompt}, {prompt_style.guards_for(editing=editing)}",
            "n": 1,
        }

        # Необязательное — только то, что эта модель понимает. Если список
        # моделей прочитать не удалось, шлём голый минимум: кадр выйдет
        # квадратным, но выйдет.
        supported = await _supported(model)
        if supported is not None:
            optional = {
                # Пропорции из запроса, если человек их выбрал: настройка —
                # умолчание, а не запрет.
                "aspect_ratio": request.params.get("aspect")
                or settings.openrouter_aspect_ratio,
                "resolution": settings.openrouter_resolution,
                "output_format": settings.openrouter_output_format,
                "quality": settings.openrouter_quality,
            }
            body.update({k: v for k, v in optional.items() if k in supported})

        # Все приложенные снимки, а не только первый: на многосубъектном кадре
        # порядок значим — модель связывает референсы с упоминаниями в промпте
        # по очереди.
        if request.references:
            body["input_references"] = [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{mime};base64," + base64.b64encode(data).decode("ascii")
                    },
                }
                for data, mime in request.references
            ]

        try:
            payload = await self._post(body)
        except GenerationUnavailable as exc:
            # У части моделей есть нижний предел по числу пикселей, и он нигде
            # не объявлен: Seedream 4.5 требует 3.7 Мп, а вертикальный кадр в
            # 1K даёт 0.59 — отказ приходит только в момент запроса. Ступенью
            # выше он проходит. Повтор ровно один и только на этой ошибке:
            # остальные отказы — повод отдать очередь следующему провайдеру, а
            # не платить дважды.
            if "resolution" not in body or not _is_too_small(str(exc)):
                raise
            logger.info("OpenRouter: %s требует кадр крупнее, повторяю в 2K", model)
            body["resolution"] = _NEXT_RESOLUTION
            payload = await self._post(body)

        data, out_mime = self._extract(payload)
        return GenerationResult(
            data=data,
            mime=out_mime,
            provider_id=self.id,
            model=model,
            cost_usd=(payload.get("usage") or {}).get("cost"),
        )

    # ─── Транспорт ───────────────────────────────────────────────────────────

    async def _post(self, body: dict) -> dict:
        headers = {
            "Authorization": f"Bearer {settings.openrouter_api_key}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=settings.openrouter_request_timeout) as client:
            resp = await client.post(
                f"{settings.openrouter_base_url.rstrip('/')}{_IMAGES_PATH}",
                json=body, headers=headers,
            )

        if resp.status_code != 200:
            raise GenerationUnavailable(
                f"OpenRouter HTTP {resp.status_code}: {resp.text[:300]}"
            )
        try:
            payload = resp.json()
        except ValueError:
            raise GenerationUnavailable("OpenRouter ответил не JSON")
        # Роутер умеет отдать 200 с ошибкой внутри — так выглядит отказ вендора,
        # до которого он достучался. Без этой ветки такой ответ уехал бы дальше
        # как пустой результат.
        if payload.get("error"):
            raise GenerationUnavailable(f"OpenRouter: {str(payload['error'])[:300]}")
        return payload

    @staticmethod
    def _extract(payload: dict) -> tuple[bytes, str]:
        items = payload.get("data") or []
        if not items:
            raise GenerationUnavailable("OpenRouter вернул ответ без картинки")
        encoded = items[0].get("b64_json")
        if not encoded:
            raise GenerationUnavailable("OpenRouter вернул элемент без b64_json")
        return base64.b64decode(encoded), items[0].get("media_type") or "image/png"
