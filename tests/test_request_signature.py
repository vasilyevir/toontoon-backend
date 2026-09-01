"""Что именно покрывает подпись запроса приложения.

Аудит: каноническая строка была `METHOD\\nPATH\\nTIMESTAMP\\nsha256(body)` —
параметры адреса в неё не входили. В перехваченном запросе их можно было менять
как угодно, и подпись оставалась верной. Для ручек, у которых вся просьба лежит
в параметрах (`?count=`, `?limit=`, `?before=`), подписано было не то, что
исполняется.

Каноническая строка живёт в ДВУХ местах: здесь и в приложении
(ToontoonApp/Networking/APISecurity.swift). Разойдясь, они не дадут приложению
достучаться вообще — поэтому меняются только вместе.

    PYTHONPATH=. .venv/bin/python -m pytest tests/test_request_signature.py -q
"""
from __future__ import annotations

import hashlib
import hmac
import time

import pytest

from app.config import settings
from app.middleware.app_key import AppKeyMiddleware


СЕКРЕТ = "as_test_secret"
КЛЮЧ = "ak_test_key"


def подписать(метод: str, путь: str, запрос: str, ts: str, тело: bytes = b"") -> str:
    canon = f"{метод}\n{путь}\n{запрос}\n{ts}\n{hashlib.sha256(тело).hexdigest()}"
    return hmac.new(СЕКРЕТ.encode(), canon.encode(), hashlib.sha256).hexdigest()


def запрос(*, метод="GET", путь="/api/styles", запрос_адреса="", подпись=None,
           тело=b"", ts=None) -> dict:
    ts = ts or str(int(time.time()))
    подпись = подпись if подпись is not None else подписать(метод, путь, запрос_адреса, ts, тело)
    return {
        "type": "http", "method": метод, "path": путь,
        "query_string": запрос_адреса.encode(),
        "headers": [
            (b"x-toontoon-app-key", КЛЮЧ.encode()),
            (b"x-toontoon-timestamp", ts.encode()),
            (b"x-toontoon-signature", подпись.encode()),
        ],
    }


@pytest.fixture(autouse=True)
def ключи(monkeypatch):
    monkeypatch.setattr(settings, "app_key", КЛЮЧ, raising=False)
    monkeypatch.setattr(settings, "app_secret", СЕКРЕТ, raising=False)


@pytest.fixture
def проверка():
    return AppKeyMiddleware(app=None)._verify


def test_a_correctly_signed_request_passes(проверка):
    assert проверка(запрос(), "/api/styles", b"") is None


def test_a_request_with_signed_query_passes(проверка):
    с = запрос(запрос_адреса="count=6")
    assert проверка(с, "/api/styles", b"") is None


def test_changing_the_query_after_signing_is_refused(проверка):
    """Главная находка: подписали одно, отправили другое.

    Подпись собрана для `count=6`, а в запросе стоит `count=12`. До правки это
    проходило — параметры в подпись не входили.
    """
    ts = str(int(time.time()))
    честная = подписать("GET", "/api/shots/daily", "count=6", ts)
    с = запрос(путь="/api/shots/daily", запрос_адреса="count=12", подпись=честная, ts=ts)
    assert проверка(с, "/api/shots/daily", b"") == "invalid request signature"


def test_appending_a_query_to_a_bare_signed_request_is_refused(проверка):
    """Подписан адрес без параметров, а параметры дописали по дороге."""
    ts = str(int(time.time()))
    честная = подписать("GET", "/api/styles", "", ts)
    с = запрос(запрос_адреса="evil=1", подпись=честная, ts=ts)
    assert проверка(с, "/api/styles", b"") == "invalid request signature"


def test_changing_the_body_after_signing_is_refused(проверка):
    ts = str(int(time.time()))
    честная = подписать("POST", "/api/chat", "", ts, '{"message":"привет"}'.encode())
    с = запрос(метод="POST", путь="/api/chat", подпись=честная, ts=ts)
    assert проверка(с, "/api/chat", '{"message":"другое"}'.encode()) == "invalid request signature"


def test_a_stale_signature_is_refused(проверка):
    старое = str(int(time.time()) - settings.app_sig_max_skew_seconds - 10)
    с = запрос(ts=старое)
    assert проверка(с, "/api/styles", b"") == "stale request signature"


def test_the_app_key_alone_is_not_enough(проверка):
    с = {"type": "http", "method": "GET", "path": "/api/styles", "query_string": b"",
         "headers": [(b"x-toontoon-app-key", КЛЮЧ.encode())]}
    assert проверка(с, "/api/styles", b"") == "request signature required"


def test_a_wrong_app_key_is_refused(проверка):
    с = запрос()
    с["headers"][0] = (b"x-toontoon-app-key", "ak_чужой".encode())
    assert проверка(с, "/api/styles", b"") == "app key required"


def test_the_two_sides_describe_the_same_canonical_string():
    """Каноническая строка собирается в двух местах, и разойтись им нельзя.

    Проверка грубая — сравниваются порядок полей в исходниках, — но ловит
    именно ту ошибку, которая иначе обнаружится на устройстве: кто-то поправил
    одну сторону и забыл вторую.
    """
    from pathlib import Path

    свифт = Path("../../arteki-ios-app/ToontoonApp/Networking/APISecurity.swift")
    if not свифт.exists():
        pytest.skip("репозиторий приложения рядом не лежит")
    строка = свифт.read_text()
    assert '"\\(method)\\n\\(path)\\n\\(query)\\n\\(ts)\\n\\(bodyHash)"' in строка, (
        "каноническая строка в приложении разошлась с серверной: "
        "METHOD\\nPATH\\nQUERY\\nTIMESTAMP\\nsha256(body)"
    )


def test_a_non_ascii_key_or_signature_does_not_crash(проверка):
    """Заголовок с не-ASCII обязан давать 401, а не пятисотку.

    `hmac.compare_digest` на СТРОКАХ с не-ASCII бросает TypeError. Заголовки
    пишет клиент — значит уронить проверку мог кто угодно одной строкой, и
    вместо честного отказа приходила ошибка сервера на каждом /api/*.

    Ровно эту ошибку уже чинили в `/health/pulse` («в байтах, а не в строках»),
    здесь она осталась. Нашлась она случайно — константы этого файла сперва
    были русскими, — и потому проверяется теперь нарочно.

    Сам ключ и секрет при этом обязаны быть ASCII: заголовки HTTP по
    спецификации latin-1, и ключ русскими буквами туда просто не доедет. Речь
    здесь только о ВХОДЯЩЕМ заголовке, который пишет кто угодно.
    """
    с = запрос()
    с["headers"][0] = (b"x-toontoon-app-key", "ак_ключ_с_кириллицей".encode())
    assert проверка(с, "/api/styles", b"") == "app key required"

    с = запрос()
    с["headers"][2] = (b"x-toontoon-signature", "подпись_буквами".encode())
    assert проверка(с, "/api/styles", b"") == "invalid request signature"
