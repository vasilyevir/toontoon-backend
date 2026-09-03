"""Докачка кадра из хранилища fal по кускам.

С нашего пути `v3b.fal.media` отдаёт ~16 КБ на соединение и замолкает; но
`Range` он поддерживает, и новое соединение получает ещё 16 КБ с любого места.
Полтора мегабайта так доехали за 66 с и 92 соединения — против отказа всегда.

Каждый тест ниже проверяет одно из правил, выведенных из того замера:
новое соединение на кусок, повтор куска по таймауту, честный отказ с адресом
и смещением, и принятие файла целиком, если хранилище `Range` не умеет.

    PYTHONPATH=. .venv/bin/python -m pytest tests/test_fal_pieces.py -q
"""
from __future__ import annotations

import httpx
import pytest

from app.config import settings
from app.services.generation.operations import GenerationUnavailable
from app.services.generation.providers.fal import FalProvider, _total_from_content_range

pytestmark = pytest.mark.asyncio

URL = "https://v3b.fal.media/files/b/x/big.png"
ТЕЛО = bytes(range(256)) * 20          # 5120 байт, каждый байт узнаваем
КУСОК = 1024


class Хранилище:
    """Отдаёт `ТЕЛО` по Range. Умеет спотыкаться на заданном смещении."""

    def __init__(self, *, умеет_range=True, спотыкается_на=None, раз=1):
        self.умеет_range = умеет_range
        self.спотыкается_на = спотыкается_на
        self.осталось_споткнуться = раз
        self.запросы: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        rng = request.headers.get("range", "")
        self.запросы.append(rng)
        if not self.умеет_range:
            return httpx.Response(200, content=ТЕЛО)
        от, до = (int(x) for x in rng.removeprefix("bytes=").split("-"))
        if self.спотыкается_на == от and self.осталось_споткнуться > 0:
            self.осталось_споткнуться -= 1
            raise httpx.ReadTimeout("стена", request=request)
        кусок = ТЕЛО[от:до + 1]
        return httpx.Response(206, content=кусок, headers={
            "content-range": f"bytes {от}-{от + len(кусок) - 1}/{len(ТЕЛО)}"})


@pytest.fixture
def подделка(monkeypatch):
    monkeypatch.setattr(settings, "fal_api_key", "k", raising=False)
    monkeypatch.setattr(settings, "fal_chunk_bytes", КУСОК, raising=False)
    monkeypatch.setattr(settings, "fal_chunk_retries", 3, raising=False)
    monkeypatch.setattr(settings, "fal_download_deadline", 30.0, raising=False)
    счёт = {"клиентов": 0}

    def поставить(сервер: Хранилище):
        транспорт = httpx.MockTransport(сервер)
        исходный = httpx.AsyncClient

        def подменённый(*a, **kw):
            счёт["клиентов"] += 1
            kw["transport"] = транспорт
            return исходный(*a, **kw)

        monkeypatch.setattr("app.services.generation.providers.fal.httpx.AsyncClient", подменённый)
        return сервер, счёт
    return поставить


async def test_собирается_из_кусков(подделка):
    сервер, _ = подделка(Хранилище())
    out = await FalProvider()._download_in_pieces(URL)
    assert out == ТЕЛО
    assert сервер.запросы == ["bytes=0-1023", "bytes=1024-2047", "bytes=2048-3071",
                              "bytes=3072-4095", "bytes=4096-5119"]


async def test_новое_соединение_на_каждый_кусок(подделка):
    """keep-alive не помогает — второй кусок по тому же соединению встаёт."""
    сервер, счёт = подделка(Хранилище())
    await FalProvider()._download_in_pieces(URL)
    assert счёт["клиентов"] == len(сервер.запросы) == 5


async def test_таймаут_куска_повторяется(подделка):
    сервер, _ = подделка(Хранилище(спотыкается_на=2048, раз=1))
    out = await FalProvider()._download_in_pieces(URL)
    assert out == ТЕЛО
    assert сервер.запросы.count("bytes=2048-3071") == 2, "кусок должен был спроситься дважды"


async def test_исчерпал_попытки_и_назвал_смещение(подделка):
    подделка(Хранилище(спотыкается_на=2048, раз=99))
    with pytest.raises(GenerationUnavailable) as e:
        await FalProvider()._download_in_pieces(URL)
    текст = str(e.value)
    assert "2048" in текст and "v3b.fal.media" in текст and "ReadTimeout" in текст
    assert "2048 Б" in текст, "должно быть сказано, сколько успело доехать"


async def test_хранилище_без_Range_отдаёт_целиком(подделка):
    сервер, счёт = подделка(Хранилище(умеет_range=False))
    out = await FalProvider()._download_in_pieces(URL)
    assert out == ТЕЛО
    assert len(сервер.запросы) == 1 and счёт["клиентов"] == 1


async def test_срок_на_файл_соблюдается(подделка, monkeypatch):
    monkeypatch.setattr(settings, "fal_download_deadline", 0.0, raising=False)
    подделка(Хранилище())
    with pytest.raises(GenerationUnavailable) as e:
        await FalProvider()._download_in_pieces(URL)
    assert "не докачался" in str(e.value)


async def test_разбор_content_range():
    assert _total_from_content_range("bytes 0-16383/16972113") == 16972113
    assert _total_from_content_range("bytes 0-1/*") is None
    assert _total_from_content_range(None) is None
    assert _total_from_content_range("мусор") is None
