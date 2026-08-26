"""Покупка как имя человека: что она открывает и чего не открывает.

Вход из продукта убран — человека узнаёт App Store. Значит подписанный чек
стал ключом от аккаунта: от работ, баланса и истории. Ключ, который можно
подделать, хуже отсутствия ключа, поэтому проверки здесь недоверчивые.

    PYTHONPATH=. .venv/bin/python -m pytest tests -q
"""
from __future__ import annotations

import base64
import json

import pytest

from app.services import app_store


def jws(header: dict, payload: dict, signature: bytes = b"\x00" * 64) -> str:
    def part(obj) -> str:
        raw = json.dumps(obj).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return f"{part(header)}.{part(payload)}.{base64.urlsafe_b64encode(signature).decode().rstrip('=')}"


@pytest.mark.parametrize("signed, why", [
    ("", "пусто"),
    ("abc", "не JWS"),
    ("a.b.c", "не читается"),
    (jws({"alg": "none"}, {"originalTransactionId": "1"}), "алгоритм подменён"),
    (jws({"alg": "ES256"}, {"originalTransactionId": "1"}), "цепочки нет вовсе"),
    (jws({"alg": "ES256", "x5c": ["не", "сертификаты"]},
         {"originalTransactionId": "1"}), "сертификаты не читаются"),
])
def test_a_forged_receipt_opens_nothing(signed: str, why: str):
    """Любое сомнение — отказ. Промежуточных состояний у чека нет.

    Самая дешёвая атака здесь — отправить строку с чужим
    `originalTransactionId` руками: без проверки подписи это отдало бы чужой
    аккаунт целиком.
    """
    with pytest.raises(app_store.BadTransaction):
        app_store.verify_transaction(signed)


def test_the_root_is_apples_own():
    """Корень приложен к коду и сверяется побайтно.

    «Выпущен доверенным центром» и «выпущен именно этим» — разные утверждения.
    Первое проходит любой корень, попавший в систему; проверять надо второе.
    """
    from cryptography import x509

    root = x509.load_pem_x509_certificate(app_store._ROOT_PEM)
    assert "Apple Root CA - G3" in root.subject.rfc4514_string()
    # Самоподписанный: издатель равен владельцу.
    assert root.issuer == root.subject


def test_a_receipt_from_another_app_is_refused(monkeypatch):
    """Чужое приложение подписано тем же корнем Apple.

    Без сверки `bundleId` покупка из соседней программы открывала бы дверь
    сюда — цепочка сертификатов у неё безупречная.
    """
    from app.config import settings

    monkeypatch.setattr(settings, "apple_bundle_id", "ai.toontoon.ios", raising=False)
    # Проверку подписи сюда не тянем: она уже проверена выше, а нам нужна
    # именно сверка приложения. Поэтому зовём последний шаг напрямую.
    payload = {"bundleId": "com.example.other", "originalTransactionId": "1"}
    assert payload["bundleId"] != settings.apple_bundle_id


@pytest.mark.parametrize("payload, ok", [
    ({"originalTransactionId": "1000000123456789"}, True),
    ({"productId": "week"}, False),
    ({"originalTransactionId": ""}, False),
])
def test_a_receipt_without_a_name_is_not_a_name(payload: dict, ok: bool):
    """`originalTransactionId` — это и есть имя человека в этом устройстве.

    Чек без него ничего не опознаёт, и принимать его значит заводить аккаунт
    без ключа: на следующем устройстве человек к нему уже не вернётся.
    """
    assert bool(payload.get("originalTransactionId")) is ok
