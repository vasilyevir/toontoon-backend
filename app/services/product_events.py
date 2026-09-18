"""События о судьбе работы — в Amplitude, из сервера.

План аналитика от 18 сентября 2026. Три события на задачу: приняли, сделали,
не вышло. Шлёт их сервер, а не приложение, по одной причине: приложение может
быть уже закрыто, когда кадр дорисовался, а знать, чем кончилась работа, нужно
про каждую, а не про те, при которых кто-то смотрел на экран.

Отсюда же и правило «одна задача — один `generation_start` и одно итоговое
событие»: точки отправки стоят там, где меняется состояние строки в базе, а
меняется оно один раз (`generations.create`, `mark_done`, `mark_failed`).

Личность — тот же `usr_…`, с которым приложение шлёт свои события: в Amplitude
это User ID, и только так серверная половина воронки склеивается с клиентской.

Без ключа `AMPLITUDE_API_KEY` всё здесь — пустые операции. Отправка идёт
фоновой задачей и никогда не роняет запрос: аналитика не повод не отдать
человеку кадр.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

import httpx

from app.config import settings

log = logging.getLogger(__name__)

_EU = "https://api.eu.amplitude.com/2/httpapi"
_US = "https://api2.amplitude.com/2/httpapi"

# Внутренняя причина отказа → короткий код для аналитика.
#
# Разбор по подстроке, как и в человеческих формулировках (`routers.generations.
# failure_text`): кода ошибки у нас нет, причина приходит текстом из разных
# мест. Два разбора рядом живут намеренно — у них разные читатели: там человек,
# которому нужно понять, что делать, здесь аналитик, которому нужно считать.
_КОДЫ: tuple[tuple[tuple[str, ...], str], ...] = (
    (("safety", "content policy", "content_policy", "content checker", "не взялась",
      "refus", "blocked", "moderation"), "content_policy"),
    (("промпт собрать нечем", "translation", "перевод недоступен"), "prompt_unavailable"),
    (("timeout", "readtimeout", "timed out"), "provider_timeout"),
    (("insufficient credits", "http 402", "no funds", "не хватает"), "insufficient_funds"),
    (("all providers failed", "unavailable", "http 5", "connection"), "provider_unavailable"),
    (("отменено", "cancel", "aborted"), "cancelled"),
    # Слова сверки, которая добивает работы, не дожившие до конца
    # (`generations.fail_stale`), — её строка приходит сюда чаще прочих.
    (("оборвалась", "не дожил", "завис", "stale", "не дождались"), "stalled"),
)
_КОД_ПО_УМОЛЧАНИЮ = "unknown_error"


def error_code(error: Optional[str]) -> str:
    """Короткий код отказа. Никогда не отдаёт наружу внутренний текст."""
    низом = (error or "").lower()
    for приметы, код in _КОДЫ:
        if any(п in низом for п in приметы):
            return код
    return _КОД_ПО_УМОЛЧАНИЮ


def photo_source(request_params: Optional[dict]) -> str:
    """Откуда взялся снимок.

    Приложение присылает это поле само — оно одно знает, нажали ли «Photo
    library» или «Take a photo». Сервер видит только ссылку на файл, и восстано-
    вить по ней источник нельзя. Чего приложение не прислало (кадр из чата,
    старая версия), считаем по профилю: он есть — рисуем по набору снимков.
    """
    params = request_params or {}
    сказано = params.get("photo_source")
    if isinstance(сказано, str) and сказано:
        return сказано
    if params.get("profile_id") or params.get("profile_ids"):
        return "ai_profile"
    return "unknown"


def _url() -> str:
    return _EU if (settings.amplitude_server_zone or "EU").upper() == "EU" else _US


def enabled() -> bool:
    return bool(settings.amplitude_api_key)


async def _send(user_id: str, event: str, props: dict[str, Any]) -> None:
    payload = {
        "api_key": settings.amplitude_api_key,
        "events": [{
            "user_id": user_id,
            "event_type": event,
            "event_properties": props,
            "time": int(time.time() * 1000),
        }],
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            ответ = await client.post(_url(), json=payload)
        if ответ.status_code >= 400:
            log.warning("Amplitude не принял %s: HTTP %s %s",
                        event, ответ.status_code, ответ.text[:200])
    except Exception:  # pragma: no cover - сеть аналитики не наше дело
        log.exception("Amplitude: событие %s не ушло", event)


def _fire(user_id: Optional[str], event: str, props: dict[str, Any]) -> None:
    """Отправить, не задерживая того, кто позвал.

    Задача отпускается намеренно: ответ Amplitude нам не нужен, а ждать его
    посреди выдачи кадра — значит платить чужой сетью за свою скорость. Ссылку
    держим, пока задача жива, иначе сборщик мусора вправе её оборвать.
    """
    if not enabled() or not user_id:
        return
    try:
        петля = asyncio.get_running_loop()
    except RuntimeError:  # вне цикла событий (тесты, скрипты) — молча мимо
        return
    задача = петля.create_task(_send(user_id, event, props))
    _летят.add(задача)
    задача.add_done_callback(_летят.discard)


_летят: set[asyncio.Task] = set()


def _общее(generation) -> dict[str, Any]:
    return {
        "template_id": generation.style_id
        or (generation.request_params or {}).get("style_id")
        or "unknown",
        "photo_source": photo_source(generation.request_params),
        "generation_id": generation.id,
    }


def started(generation) -> None:
    """Задача принята — значит, она есть и за неё уже списано."""
    _fire(generation.user_id, "generation_start", _общее(generation))


def completed(generation) -> None:
    """Кадр создан, сохранён и доступен человеку."""
    _fire(generation.user_id, "generation_completed", _общее(generation))


def failed(generation, error: Optional[str] = None) -> None:
    """Окончательный отказ. Промежуточные, после которых работа продолжается,
    сюда не приходят: точка отправки стоит там, где строка становится
    `failed`."""
    props = _общее(generation)
    props["generation_error_code"] = error_code(error if error is not None else generation.error)
    _fire(generation.user_id, "generation_failed", props)
