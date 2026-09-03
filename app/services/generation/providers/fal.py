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
import json
import logging
import time
from typing import Optional

import httpx

from app.config import settings
from app.services import prompt_style
from app.services.generation.operations import (
    ContentRefused,
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

# Диалекты. У fal один транспорт на все модели, но ВХОД у каждой свой, и
# «просто добавить строку в реестр» здесь не работает: nano-banana понимает
# `aspect_ratio: "9:16"`, а gpt-image-2 такого поля не имеет вовсе — у него
# `image_size` с пресетами и `quality`, где умолчание `high` стоит вчетверо
# дороже `medium` ($0.165 против $0.041 за портретный кадр).
#
# Поэтому форма кадра переводится на язык модели, а не отправляется как есть,
# и у модели могут быть свои обязательные добавки к телу. Модель, которой нет
# в таблице, получает поведение nano-banana — оно и было единственным до сих
# пор, и все тесты писались под него.
# Явные размеры, а не пресеты fal. Пресет `portrait_16_9` даёт 608×1088 — кадр
# на четверть мельче того, что люди видели весь август через OpenRouter
# (864×1536 — 125 кадров, 1152×1536 — 104). Задача — вернуть именно те
# размеры, поэтому здесь те же числа, что стояли у OpenRouter, а не «примерно
# такая же форма». Проверено на fal: `{"width","height"}` принимаются как
# есть, время в `medium` 38–44 с.
_GPT_IMAGE_SIZE = {
    "9:16": {"width": 864,  "height": 1536},
    "16:9": {"width": 1536, "height": 864},
    "2:3":  {"width": 1024, "height": 1536},
    "3:2":  {"width": 1536, "height": 1024},
    "3:4":  {"width": 1152, "height": 1536},
    "4:3":  {"width": 1536, "height": 1152},
    "4:5":  {"width": 1152, "height": 1440},
    "5:4":  {"width": 1440, "height": 1152},
    "1:1":  {"width": 1024, "height": 1024},
}
_DIALECT: dict[str, dict] = {
    "openai/gpt-image-2/edit": {
        "size_field": "image_size", "size_map": _GPT_IMAGE_SIZE, "size_default": "auto",
        # `medium` сознательно: `high` — умолчание fal и самый дорогой режим.
        # Поднимать до `high` только после того, как medium увидят глазами.
        "extra": {"quality": "medium", "output_format": "png"},
        # Что из `request.params` можно пробросить как есть — и под каким именем.
        # `quality` — тот же ключ; `size` — явные {width, height}, они у gpt-image-2
        # заменяют пресет: так подгоняется размер под тот, что давал OpenRouter.
        "passthrough": {"quality": "quality", "size": "image_size"},
    },
    "openai/gpt-image-2": {
        "size_field": "image_size", "size_map": _GPT_IMAGE_SIZE, "size_default": "auto",
        "extra": {"quality": "medium", "output_format": "png"},
        "passthrough": {"quality": "quality", "size": "image_size"},
    },
    # Те же nano-banana, но с выбором разрешения: 1K (умолчание fal) / 2K / 4K.
    # Форму они понимают как обычный `aspect_ratio`, поэтому size_field не свой.
    "fal-ai/nano-banana-pro/edit": {"passthrough": {"resolution": "resolution"}},
    "fal-ai/nano-banana-2/edit":   {"passthrough": {"resolution": "resolution"}},
    "fal-ai/nano-banana-pro":      {"passthrough": {"resolution": "resolution"}},
}


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


def _total_from_content_range(значение: Optional[str]) -> Optional[int]:
    """`bytes 0-16383/16972113` → 16972113. Всё остальное → None."""
    if not значение or "/" not in значение:
        return None
    хвост = значение.rsplit("/", 1)[1].strip()
    return int(хвост) if хвост.isdigit() else None



def _is_fal_host(host: str | None) -> bool:
    """Узел из `fal_download_hosts` или его поддомен — и ничего больше."""
    if not host:
        return False
    host = host.lower().rstrip(".")
    for суффикс in settings.fal_download_hosts.split(","):
        суффикс = суффикс.strip().lower()
        if суффикс and (host == суффикс or host.endswith("." + суффикс)):
            return True
    return False



def _refused_by_content(status_code: int, text: str) -> bool:
    """422 с `content_policy_violation` — отказ по содержанию, а не по параметрам.

    Обычная 422 у fal — «поле не то» (наша ошибка, чинить нам). Эта — модель
    посмотрела на снимок или промпт и не взялась. Различать их важно: первую
    можно и нужно обойти другим исполнителем, вторую — нельзя.
    """
    return status_code == 422 and "content_policy_violation" in (text or "")


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

        # Пропорции шлём всегда — как и адаптер OpenRouter.
        #
        # Здесь стояло «только когда рисуем с нуля», и обоснование было ложным.
        # Первый замер показал: с `aspect_ratio` 0 удач из 4, без него 2 из 4, —
        # и я записал в виновные пропорции. На деле рядом стоял настоящий
        # виновник (крикливый запрет про водяной знак, см. ниже), а пропорции
        # просто попали под руку. После его смягчения замер честный:
        #
        #     без aspect_ratio   6/6
        #     с aspect_ratio     6/6
        #
        # Документация это подтверждает: `9:16` — законное значение перечисления
        # (auto, 21:9, 16:9, 3:2, 4:3, 5:4, 1:1, 4:5, 3:4, 2:3, 9:16).
        #
        # А раз выбор свободен, он должен совпадать с OpenRouter: одна и та же
        # карточка каталога обязана давать кадр одной формы независимо от того,
        # кто из исполнителей его нарисовал. Разная форма у одного стиля — это
        # то, что человек заметит, а объяснить нечем.
        aspect = request.params.get("aspect") or settings.fal_aspect_ratio
        диалект = _DIALECT.get(model) or {}
        if "size_field" in диалект:
            # Модель говорит на своём: форму переводим, добавки кладём.
            body[диалект["size_field"]] = диалект["size_map"].get(aspect, диалект["size_default"])
        elif aspect:
            body["aspect_ratio"] = aspect
        body.update(диалект.get("extra") or {})
        # Явные параметры вызова сильнее умолчаний диалекта — так матрица
        # «1K/2K/4K, medium/high, точный размер» гоняется одним адаптером.
        for наш, их in (диалект.get("passthrough") or {}).items():
            if request.params.get(наш) is not None:
                body[их] = request.params[наш]

        # Кадр — в ответе, а не ссылкой на хранилище fal.
        #
        # По умолчанию fal кладёт готовую картинку на свой CDN и присылает
        # адрес, а мы идём его качать. Этот последний шаг и оказался единственным
        # ненадёжным звеном: заказ, ожидание и результат отвечали за доли
        # секунды, а `v3b.fal.media` отдавал первые пятнадцать килобайт и
        # замолкал. Пятнадцать килобайт — это начальное окно TCP: первая пачка
        # доходит, дальше путь мёртв. Одинаково на HTTP/2 и HTTP/1.1, на IPv4 и
        # IPv6, то есть дело не в протоколе.
        #
        # `sync_mode` убирает этот шаг совсем: картинка приезжает внутри JSON
        # тем же соединением, что и всё остальное. Мы всё равно скачивали её
        # немедленно и клали к себе — держать её у них было незачем.
        #
        # Заодно это честнее к обещанию про лица: без `sync_mode` готовый кадр
        # обязан полежать на чужом CDN, пусть и по недолгой ссылке.
        #
        # Ссылку разбирать не перестали: модель вправе не знать про `sync_mode`,
        # и тогда придёт адрес. Тогда работает прежний путь — со всеми его
        # рисками, но лучше медленно, чем никак.
        body["sync_mode"] = True

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
            # Цены здесь нет, и это не упущение адаптера.
            #
            # fal её в ответе не отдаёт вовсе — проверено живым запросом: в
            # ответе только `images` и `description`, в статусе только
            # `metrics.inference_time`. Стоимость лежит в отдельном Usage API
            # платформы и в отчётах кабинета.
            #
            # Это ПОТЕРЯ по сравнению с OpenRouter, который кладёт `usage.cost`
            # прямо в ответ, и терять её молча нельзя: на этом числе держится
            # вопрос «сколько стоит упрямство модели», ради которого колонка
            # `provider_cost_usd` и заведена. Пока fal основной, у его работ она
            # будет пустой, и сравнивать вендоров по цене придётся не по нашей
            # базе, а по их кабинету.
            #
            # Подставлять сюда оценку из прайса нельзя: поле называется «что
            # кадр стоил», а не «что он примерно стоил», и догадка, записанная
            # как факт, хуже отсутствия числа.
            cost_usd=None,
        )

    # ─── Транспорт ───────────────────────────────────────────────────────────

    @property
    def _headers(self) -> dict:
        # Именно `Key`, а не `Bearer`: у fal своя схема, и с `Bearer` приходит
        # 401 без объяснений.
        return {
            "Authorization": f"Key {settings.fal_api_key}",
            "Content-Type": "application/json",
            # Не хранить у себя наши запросы. По умолчанию fal держит тела
            # запросов и ответов ТРИДЦАТЬ ДНЕЙ — а в теле у нас лицо человека,
            # уехавшее base64. То есть без этой строки мы месяц держим чужие
            # лица на чужих серверах, в истории, доступной их админскому ключу.
            #
            # Это перечёркивало бы всё остальное, что здесь сделано ради этих
            # снимков: закрытый бакет, отдача через проверку владельца, срезание
            # координат из EXIF, срок хранения, стирание при удалении аккаунта.
            # Обещание «ваше лицо не разлетается» стоит ровно столько, сколько
            # стоит самое слабое звено.
            "X-Fal-Store-IO": "0",
            # И кадру на их CDN — короткий срок. Мы забираем его сразу; ссылка,
            # живущая дольше, нужна только тому, кто её найдёт.
            "X-Fal-Object-Lifecycle-Preference": json.dumps(
                {"expiration_duration_seconds": settings.fal_media_ttl_seconds}
            ),
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
        if _refused_by_content(resp.status_code, resp.text):
            raise ContentRefused(f"fal: модель не взялась за снимок, HTTP 422: {resp.text[:300]}")
        if resp.status_code >= 300:
            raise GenerationUnavailable(f"fal HTTP {resp.status_code}: {resp.text[:300]}")
        try:
            payload = resp.json()
            request_id = str(payload["request_id"])
        except (ValueError, KeyError, TypeError) as exc:
            raise GenerationUnavailable(
                f"fal не вернул request_id: {resp.text[:200]}") from exc

        свой_статус, свой_ответ = self._fallback_urls(model, request_id)
        статус = str(payload.get("status_url") or свой_статус)
        ответ = str(payload.get("response_url") or свой_ответ)
        # В эти запросы уходит `Authorization: Key …`. Адреса присланы самим
        # fal, но если в них окажется чужой узел — ключ уедет туда. Свои
        # собранные адреса тогда надёжнее присланных.
        наш = httpx.URL(settings.fal_base_url).host
        if httpx.URL(статус).host != наш:
            статус = свой_статус
        if httpx.URL(ответ).host != наш:
            ответ = свой_ответ
        return request_id, статус, ответ

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
        # Отказ по содержанию приходит именно здесь: очередь заказ приняла,
        # статус дошёл до COMPLETED, а результат — 422 с content_policy_violation.
        if _refused_by_content(resp.status_code, resp.text):
            raise ContentRefused(f"fal: модель не взялась за снимок, HTTP 422: {resp.text[:200]}")
        if resp.status_code >= 300:
            raise GenerationUnavailable(
                f"fal: результат недоступен, HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            return resp.json()
        except ValueError as exc:
            raise GenerationUnavailable("fal ответил не JSON") from exc

    async def _first_image(self, payload: dict) -> tuple[bytes, str]:
        """Достать байты кадра из ответа fal.

        Поле называется `url`, но лежать в нём может двоякое, и порядок разбора
        отражает, что теперь основное.

        С `sync_mode` (мы просим его всегда) там сам кадр строкой
        `data:image/png;base64,…`. Скачивать нечего, сети не нужно.

        Без него — адрес в хранилище fal, и тогда работает прежний путь. Он
        остаётся ради моделей, которые про `sync_mode` не знают, но он же и
        единственное место здесь, где мы зависим от чужого CDN: ровно на нём
        всё и встало, когда `v3b.fal.media` начал отдавать по полкилобайта в
        секунду. Поэтому отказ на этом шаге называет и шаг, и узел — иначе в
        журнале остаётся голое `ReadTimeout`, по которому не понять, что
        генерация-то удалась.
        """
        images = payload.get("images") or []
        if not images or not isinstance(images, list):
            raise GenerationUnavailable(f"fal вернул ответ без кадра: {str(payload)[:200]}")
        first = images[0] or {}
        url = first.get("url")
        if not url:
            raise GenerationUnavailable("fal вернул кадр без ссылки")

        # С `sync_mode` в поле `url` лежит не адрес, а сам кадр — строкой
        # `data:image/png;base64,…`. Скачивать нечего: он уже здесь.
        #
        # Проверка перед сетью, а не после: без неё `client.get` попытался бы
        # сходить по «адресу» длиной в полтора мегабайта.
        if url.startswith("data:"):
            голова, _, тело = url.partition(",")
            if not тело:
                raise GenerationUnavailable("fal вернул пустой кадр в ответе")
            if len(тело) > settings.fal_download_max_bytes * 4 // 3:
                raise GenerationUnavailable("fal вернул кадр больше допустимого")
            try:
                сырьё = base64.b64decode(тело, validate=True)
            except Exception as exc:  # noqa: BLE001 — битая строка это отказ
                raise GenerationUnavailable("fal вернул кадр, который не разбирается") from exc
            mime = голова[5:].split(";", 1)[0] or "image/png"
            return сырьё, mime

        # Скачивание названо в отказе отдельно, и это не украшательство.
        #
        # Когда оно молча падало, в журнале стояло `ReadTimeout('')` — без шага,
        # без адреса, без времени. По такой строке видно только, что «что-то не
        # ответило за тридцать секунд», и разобраться в ней нельзя: заказ,
        # шесть опросов состояния и скачивание идут одним клиентом с одним
        # лимитом. Настоящий виновник нашёлся лишь прогоном адаптера вручную:
        # хранилище fal отдавало кадр по 500 байт в секунду — пятнадцать
        # килобайт, и стоп. С той же машины cdnjs шёл под сто килобайт в
        # секунду, то есть канал был ни при чём.
        #
        # Теперь отказ называет шаг, узел и сколько успело дотечь. Разбирать
        # такое — минута, а не полдня.
        # Сюда попадают только те, кто `sync_mode` не уважил, — а просим мы
        # его всегда. Значит модель его не поддерживает, и кадр поедет через
        # хранилище fal со всеми его рисками.
        #
        # Проверено: `sync_mode` действует не на платформу, а на МОДЕЛЬ.
        # `nano-banana` и `nano-banana/edit` его уважают, а апскейлеры
        # (`esrgan`, `aura-sr`) молча возвращают ссылку — и на картинке
        # 64×64 тоже, то есть дело не в размере ответа.
        #
        # Молчать об этом нельзя: съезд на хрупкий путь ничем не отличается от
        # обычной работы, пока путь жив. Заметить его надо в тот день, когда в
        # реестр добавят новую модель, а не в тот, когда она начнёт падать.
        logger.warning(
            "fal: модель %s вернула ссылку, хотя просили sync_mode — "
            "кадр поедет через хранилище (%s)",
            self.model, httpx.URL(url).host,
        )
        сырьё = await self._download_in_pieces(url)
        mime = first.get("content_type") or "image/png"
        return сырьё, str(mime).split(";", 1)[0].strip()

    async def _download_in_pieces(self, url: str) -> bytes:
        """Скачать кадр из хранилища fal кусками, новым соединением на каждый.

        Одним запросом с нашего пути не доезжает ничего: `v3b.fal.media`
        отдаёт первые ~16 КБ и замолкает — на любом протоколе, на любой
        версии IP. Но `Range` он поддерживает, и НОВОЕ соединение получает
        ещё 16 КБ с любого смещения. Проверено на полутора мегабайтах:
        92 соединения, 66 секунд, ноль ошибок — против отказа в 100% случаев.

        Отсюда три правила, и каждое из них — из замера, а не из вкуса:
          • новый `AsyncClient` на каждый кусок: keep-alive не помогает,
            второй кусок по тому же соединению встаёт;
          • таймаут на КУСОК, не на файл: стена видна за секунды, а не
            через полминуты;
          • размер куска — настройка: он равен тому, что доезжает, и на
            другом пути может быть другим.

        Если хранилище ответило 200 вместо 206 — оно не умеет `Range` и
        отдало файл целиком. Это не ошибка, а удача: берём и выходим.
        """
        адрес = httpx.URL(url)
        узел = адрес.host
        # Адрес пришёл от fal, но проверяем его как чужой: https и только их
        # хранилище. Иначе подмена ответа или `FAL_BASE_URL` превращает
        # докачку в запрос к любому узлу, до которого дотягивается сервер.
        if адрес.scheme != "https" or not _is_fal_host(узел):
            raise GenerationUnavailable(f"fal: кадр лежит не в хранилище fal ({узел})")
        предел = settings.fal_download_max_bytes
        кусок = settings.fal_chunk_bytes
        deadline = time.monotonic() + settings.fal_download_deadline
        буфер = bytearray()
        всего: Optional[int] = None

        while всего is None or len(буфер) < всего:
            if time.monotonic() > deadline:
                raise GenerationUnavailable(
                    f"fal: кадр не докачался за {settings.fal_download_deadline:.0f} с "
                    f"с {узел} — получено {len(буфер)} Б из {всего if всего else '?'}")
            от, до = len(буфер), len(буфер) + кусок - 1
            попытка = 0
            while True:
                попытка += 1
                try:
                    async with httpx.AsyncClient(timeout=settings.fal_chunk_timeout) as client:
                        resp = await client.get(url, headers={"Range": f"bytes={от}-{до}"})
                    break
                except httpx.TransportError as exc:
                    if попытка >= settings.fal_chunk_retries:
                        raise GenerationUnavailable(
                            f"fal: кусок {от}–{до} не доехал с {узел} за {попытка} попыток "
                            f"({type(exc).__name__}); получено {len(буфер)} Б") from exc

            if resp.status_code == 200:
                if от != 0:
                    raise GenerationUnavailable(
                        f"fal: {узел} прислал файл целиком посреди докачки")
                if len(resp.content) > предел:
                    raise GenerationUnavailable(f"fal: кадр с {узел} больше {предел} Б")
                return bytes(resp.content)
            if resp.status_code != 206:
                raise GenerationUnavailable(
                    f"fal: кадр не скачался, HTTP {resp.status_code} от {узел}")
            if всего is None:
                всего = _total_from_content_range(resp.headers.get("content-range"))
                if not всего:
                    raise GenerationUnavailable(f"fal: {узел} не назвал размер файла")
                if всего > предел:
                    raise GenerationUnavailable(f"fal: кадр с {узел} больше {предел} Б")
            if not resp.content:
                raise GenerationUnavailable(f"fal: пустой кусок от {узел} на смещении {от}")
            if len(буфер) + len(resp.content) > предел:
                raise GenerationUnavailable(f"fal: {узел} отдаёт больше, чем обещал")
            буфер += resp.content
        return bytes(буфер)
