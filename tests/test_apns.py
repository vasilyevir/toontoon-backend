"""Пуши на iPhone: что уходит в Apple и что остаётся у нас.

Задача #6 Андрея (21 сентября 2026): пуш после генерации не приходил — его
присылало само приложение, пока было открыто. Теперь шлёт сервер, и здесь
проверяется то, что без телефона не проверить руками: подпись, адрес Apple
для каждого окружения, заголовки, забывание мёртвых адресов и молчание без
ключа. Сеть и Redis подменены.

    PYTHONPATH=. .venv/bin/python -m pytest tests/test_apns.py -q
"""
from __future__ import annotations

import base64
import json

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from app.config import settings
from app.services import apns

TOKEN_A = "a" * 64
TOKEN_B = "b" * 64


class ПамятьRedis:
    def __init__(self):
        self.данные = {}

    async def get(self, key):
        return self.данные.get(key)

    async def set(self, key, value):
        self.данные[key] = value


@pytest.fixture
def ключ(monkeypatch):
    """Настоящий ключ P-256, как у Apple, — только наш, тестовый."""
    private = ec.generate_private_key(ec.SECP256R1())
    pem = private.private_bytes(serialization.Encoding.PEM,
                                serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
    monkeypatch.setattr(settings, "apns_key_id", "KEYID12345")
    monkeypatch.setattr(settings, "apns_team_id", "3GU8WB3N29")
    monkeypatch.setattr(settings, "apns_key_b64", base64.b64encode(pem).decode())
    monkeypatch.setattr(settings, "apns_topic", "mobile.atom.toontoon")
    monkeypatch.setattr(apns, "_provider", None)
    return private.public_key()


@pytest.fixture
def redis(monkeypatch):
    память = ПамятьRedis()
    monkeypatch.setattr(apns, "_redis", lambda: память)
    return память


def apple(monkeypatch, отвечает):
    """Подменить Apple: `отвечает(request) -> httpx.Response`. Возвращает журнал."""
    журнал = []

    def handler(request):
        журнал.append(request)
        return отвечает(request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(apns, "_http", lambda: client)
    return журнал


# ── Подпись ──────────────────────────────────────────────────────────────────

def test_токен_провайдера_подписан_ключом_и_назван_по_нему(ключ):
    token = apns._provider_token(now=1_000_000)
    заголовок = jwt.get_unverified_header(token)
    assert заголовок["alg"] == "ES256" and заголовок["kid"] == "KEYID12345"
    содержимое = jwt.decode(token, ключ, algorithms=["ES256"])
    assert содержимое == {"iss": "3GU8WB3N29", "iat": 1_000_000}


def test_токен_живёт_сорок_минут(ключ):
    """Чаще обновлять — Apple откажет (TooManyProviderTokenUpdates), реже часа — тоже."""
    первый = apns._provider_token(now=1_000_000)
    assert apns._provider_token(now=1_000_000 + 39 * 60) == первый
    assert apns._provider_token(now=1_000_000 + 41 * 60) != первый


# ── Адреса телефонов ─────────────────────────────────────────────────────────

async def test_адрес_запоминается_без_дублей(redis):
    await apns.remember_device("usr_1", TOKEN_A, "sandbox")
    await apns.remember_device("usr_1", TOKEN_A, "production")  # тот же телефон
    await apns.remember_device("usr_1", TOKEN_B, "production")
    assert await apns.devices("usr_1") == [
        {"token": TOKEN_A, "env": "production"},
        {"token": TOKEN_B, "env": "production"},
    ]


async def test_незнакомое_окружение_отвергается(redis):
    with pytest.raises(ValueError):
        await apns.remember_device("usr_1", TOKEN_A, "staging")


# ── Отправка ─────────────────────────────────────────────────────────────────

async def test_уведомление_уходит_на_адрес_своего_окружения(ключ, redis, monkeypatch):
    await apns.remember_device("usr_1", TOKEN_A, "sandbox")
    await apns.remember_device("usr_1", TOKEN_B, "production")
    журнал = apple(monkeypatch, lambda r: httpx.Response(200))

    дошло = await apns.notify("usr_1", "Your Café Fashion picture is ready",
                              "Open Toontoon to see it.",
                              data={"generation_id": "gen_1"}, collapse_id="gen_1")
    assert дошло == 2
    адреса = sorted(str(r.url) for r in журнал)
    assert адреса == [f"https://api.push.apple.com/3/device/{TOKEN_B}",
                      f"https://api.sandbox.push.apple.com/3/device/{TOKEN_A}"]

    запрос = журнал[0]
    assert запрос.headers["apns-topic"] == "mobile.atom.toontoon"
    assert запрос.headers["apns-push-type"] == "alert"
    assert запрос.headers["apns-collapse-id"] == "gen_1"
    assert запрос.headers["authorization"].startswith("bearer ")
    тело = json.loads(запрос.content)
    assert тело["aps"]["alert"] == {"title": "Your Café Fashion picture is ready",
                                    "body": "Open Toontoon to see it."}
    assert тело["generation_id"] == "gen_1"


@pytest.mark.parametrize("ответ", [
    httpx.Response(410, json={"reason": "Unregistered"}),
    httpx.Response(400, json={"reason": "BadDeviceToken"}),
    httpx.Response(400, json={"reason": "DeviceTokenNotForTopic"}),
])
async def test_мёртвый_адрес_забывается(ключ, redis, monkeypatch, ответ):
    """Приложение удалили или токен из другого окружения — слать туда незачем."""
    await apns.remember_device("usr_1", TOKEN_A, "production")
    apple(monkeypatch, lambda r: ответ)
    assert await apns.notify("usr_1", "t", "b") == 0
    assert await apns.devices("usr_1") == []


async def test_временный_отказ_адрес_не_стирает(ключ, redis, monkeypatch):
    await apns.remember_device("usr_1", TOKEN_A, "production")
    apple(monkeypatch, lambda r: httpx.Response(503, json={"reason": "ServiceUnavailable"}))
    assert await apns.notify("usr_1", "t", "b") == 0
    assert len(await apns.devices("usr_1")) == 1


async def test_без_ключа_не_уходит_ничего(redis, monkeypatch):
    monkeypatch.setattr(settings, "apns_key_b64", "")
    await apns.remember_device("usr_1", TOKEN_A, "production")
    журнал = apple(monkeypatch, lambda r: httpx.Response(200))
    assert await apns.notify("usr_1", "t", "b") == 0
    apns.fire("usr_1", "t", "b")
    assert журнал == []
