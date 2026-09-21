"""Пуши на iPhone — через APNs, с сервера.

Задача #6 Андрея (21 сентября 2026): после генерации пуш не приходил. Его и
не было: уведомление ставило само приложение, пока опрашивало сервер, а
свёрнутое приложение iOS замораживает за секунды — кадр дорисовывался через
полминуты, и сообщить было некому. `push_service` рядом — это Web Push для
браузеров, телефону он ничего не шлёт.

Теперь о готовом кадре сообщает сервер: он один знает, когда работа
кончилась, и знает это при закрытом приложении.

Устройство:

* приложение отдаёт адрес телефона (device token) вместе с окружением —
  `sandbox` у отладочной сборки, `production` у TestFlight и App Store; у них
  разные адреса Apple, и токен одного не принимается другим;
* адреса живут в Redis, как и веб-подписки: они одноразовые по смыслу —
  приложение присылает свежий при каждом запуске, а мёртвый Apple называет
  сам (410 / BadDeviceToken), и мы его забываем;
* подпись — ключом APNs (.p8, ES256), токен провайдера живёт до часа, мы
  обновляем его раньше.

Без ключа (`APNS_KEY_ID`, `APNS_KEY_B64`) всё здесь — пустые операции.
Отправка идёт фоном и никогда не мешает работе, о которой сообщает.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from typing import Any, Optional

import httpx
import jwt

from app.config import settings

log = logging.getLogger("toontoon.apns")

HOSTS = {
    "production": "https://api.push.apple.com",
    "sandbox": "https://api.sandbox.push.apple.com",
}
ENVIRONMENTS = tuple(HOSTS)

_DEVICES = "apns:"          # apns:{user_id} → JSON-список {token, env}
_MAX_DEVICES = 10           # телефон, планшет, переустановки — с запасом
_TOKEN_TTL = 40 * 60        # Apple принимает токен провайдера до часа

# Ответы Apple, после которых адрес мёртв навсегда: приложение удалили,
# токен из другого окружения, чужое приложение.
_DEAD = {"BadDeviceToken", "Unregistered", "DeviceTokenNotForTopic"}

_provider: Optional[tuple[float, str]] = None
_client: Optional[httpx.AsyncClient] = None
_летят: set[asyncio.Task] = set()


def enabled() -> bool:
    return bool(settings.apns_key_id and settings.apns_team_id and settings.apns_key_b64)


def _provider_token(now: Optional[float] = None) -> str:
    """Подписанный токен провайдера. Один на всех, обновляется каждые 40 минут.

    Apple отказывает, если токен обновлять слишком часто (TooManyProviderTokenUpdates),
    и если он старше часа (ExpiredProviderToken) — отсюда кэш посередине.
    """
    global _provider
    now = now or time.time()
    if _provider and now - _provider[0] < _TOKEN_TTL:
        return _provider[1]
    key = base64.b64decode(settings.apns_key_b64).decode()
    token = jwt.encode({"iss": settings.apns_team_id, "iat": int(now)}, key,
                       algorithm="ES256", headers={"kid": settings.apns_key_id})
    _provider = (now, token)
    return token


def _http() -> httpx.AsyncClient:
    """Одно соединение по HTTP/2 — иначе APNs не разговаривает."""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(http2=True, timeout=10)
    return _client


# ── Адреса телефонов ─────────────────────────────────────────────────────────

def _redis():
    from app.redis_client import get_client
    return get_client()


async def devices(user_id: str) -> list[dict]:
    raw = await _redis().get(_DEVICES + user_id)
    return json.loads(raw) if raw else []


async def remember_device(user_id: str, token: str, environment: str) -> None:
    """Запомнить адрес телефона. Тот же адрес второй раз — не дубль, а обновление."""
    if environment not in ENVIRONMENTS:
        raise ValueError(f"unknown APNs environment: {environment}")
    known = [d for d in await devices(user_id) if d.get("token") != token]
    known.append({"token": token, "env": environment})
    await _redis().set(_DEVICES + user_id, json.dumps(known[-_MAX_DEVICES:]))


async def forget_device(user_id: str, token: str) -> None:
    known = [d for d in await devices(user_id) if d.get("token") != token]
    await _redis().set(_DEVICES + user_id, json.dumps(known))


# ── Отправка ─────────────────────────────────────────────────────────────────

async def _send_one(device: dict, payload: dict, collapse_id: Optional[str]) -> tuple[int, str]:
    headers = {
        "authorization": f"bearer {_provider_token()}",
        "apns-topic": settings.apns_topic,
        "apns-push-type": "alert",
        "apns-priority": "10",
    }
    if collapse_id:
        headers["apns-collapse-id"] = collapse_id[:64]
    response = await _http().post(f"{HOSTS[device['env']]}/3/device/{device['token']}",
                                  headers=headers, json=payload)
    reason = ""
    if response.status_code != 200:
        try:
            reason = response.json().get("reason", "")
        except Exception:  # noqa: BLE001 — тело ответа не обязано быть JSON
            reason = response.text[:100]
    return response.status_code, reason


async def notify(user_id: str, title: str, body: str, *,
                 data: Optional[dict[str, Any]] = None,
                 collapse_id: Optional[str] = None) -> int:
    """Отправить уведомление на все телефоны человека. Возвращает, скольким дошло."""
    if not enabled():
        return 0
    payload: dict[str, Any] = {"aps": {"alert": {"title": title, "body": body},
                                       "sound": "default"}}
    if data:
        payload.update(data)
    delivered = 0
    for device in await devices(user_id):
        try:
            status, reason = await _send_one(device, payload, collapse_id)
        except Exception:  # noqa: BLE001 — сеть до Apple не наше дело
            log.warning("APNs: не отправилось", exc_info=True)
            continue
        if status == 200:
            delivered += 1
        elif status == 410 or reason in _DEAD:
            await forget_device(user_id, device["token"])
        else:
            log.warning("APNs отказал: HTTP %s %s", status, reason)
    return delivered


def fire(user_id: str, title: str, body: str, **kwargs: Any) -> None:
    """Отправить, не задерживая того, кто позвал. Вне цикла событий — мимо."""
    if not enabled():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task = loop.create_task(_notify_quietly(user_id, title, body, **kwargs))
    _летят.add(task)
    task.add_done_callback(_летят.discard)


async def _notify_quietly(user_id: str, title: str, body: str, **kwargs: Any) -> None:
    try:
        await notify(user_id, title, body, **kwargs)
    except Exception:  # noqa: BLE001 — пуш не повод ронять работу
        log.warning("APNs: уведомление не ушло", exc_info=True)
