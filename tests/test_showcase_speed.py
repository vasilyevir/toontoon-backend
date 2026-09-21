"""Главная при первом запуске: картинки витрины — быстро.

Задача #7 Андрея (21 сентября 2026): верхние карточки главной не успевали
появиться до экрана награды на седьмой секунде. После переезда хранилища в
Yandex Object Storage (Казахстан) каждая витринная картинка стоила 0,5–2,5 с,
а одна повисла на 30 с. Причин было две, и каждая проверяется здесь:

* на каждое чтение создавался новый клиент S3 — новое TLS-соединение через
  границу; теперь соединение одно на процесс;
* витрина читалась из хранилища на каждый запрос, хотя она неизменна и
  одинакова для всех; теперь она в памяти.

    PYTHONPATH=. .venv/bin/python -m pytest tests/test_showcase_speed.py -q
"""
from __future__ import annotations

import pytest

from app.routers import styles
from app.storage.s3 import S3Storage


@pytest.fixture(autouse=True)
def чистая_витрина(monkeypatch):
    monkeypatch.setattr(styles, "_examples", styles.OrderedDict())
    monkeypatch.setattr(styles, "_examples_size", 0)


class Хранилище:
    def __init__(self):
        self.чтений = 0

    async def get(self, key):
        self.чтений += 1
        return f"bytes-of-{key}".encode()


async def test_витрина_читается_из_хранилища_один_раз(monkeypatch):
    хранилище = Хранилище()
    monkeypatch.setattr(styles, "get_storage", lambda: хранилище)
    for _ in range(5):
        assert await styles._example_bytes("catalog/a.jpg") == b"bytes-of-catalog/a.jpg"
    assert хранилище.чтений == 1


async def test_пропавшая_картинка_не_запоминается(monkeypatch):
    class Пусто:
        чтений = 0

        async def get(self, key):
            self.чтений += 1
            return None

    пусто = Пусто()
    monkeypatch.setattr(styles, "get_storage", lambda: пусто)
    assert await styles._example_bytes("catalog/gone.jpg") is None
    assert await styles._example_bytes("catalog/gone.jpg") is None
    assert пусто.чтений == 2, "отсутствие не кэшируется: картинку могут дозалить"


def test_потолок_вытесняет_давно_не_спрошенное(monkeypatch):
    monkeypatch.setattr(styles, "_EXAMPLES_CAP", 10)
    styles._remember_example("a", b"12345")
    styles._remember_example("b", b"12345")
    styles._examples.move_to_end("a")          # «a» спросили недавно
    styles._remember_example("c", b"12345")    # не влезает — уходит «b»
    assert list(styles._examples) == ["a", "c"]
    assert styles._examples_size == 10


# ── Одно соединение с хранилищем ─────────────────────────────────────────────

class Клиент:
    открыто = 0
    закрыто = 0

    async def __aenter__(self):
        Клиент.открыто += 1
        return self

    async def __aexit__(self, *exc):
        Клиент.закрыто += 1


async def test_соединение_с_хранилищем_одно_на_процесс(monkeypatch):
    Клиент.открыто = Клиент.закрыто = 0
    хранилище = S3Storage()
    monkeypatch.setattr(хранилище, "_new_client", lambda: Клиент())

    клиенты = []
    for _ in range(10):
        async with хранилище._client() as c:
            клиенты.append(c)

    assert Клиент.открыто == 1, "на каждый запрос открывалось новое соединение"
    assert all(c is клиенты[0] for c in клиенты)

    await хранилище.close()
    assert Клиент.закрыто == 1


async def test_разом_тоже_одно_соединение(monkeypatch):
    """Главная просит десятки картинок одновременно — соединение всё равно одно."""
    import asyncio

    Клиент.открыто = 0
    хранилище = S3Storage()
    monkeypatch.setattr(хранилище, "_new_client", lambda: Клиент())

    async def прочесть():
        async with хранилище._client() as c:
            await asyncio.sleep(0)
            return c

    клиенты = await asyncio.gather(*(прочесть() for _ in range(20)))
    assert Клиент.открыто == 1
    assert len({id(c) for c in клиенты}) == 1
    await хранилище.close()
