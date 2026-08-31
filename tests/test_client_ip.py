"""Чей адрес считают ограничители.

Аудит показал на живом сервере, что ограничителей по адресу фактически не было:
четырнадцать запросов с одного адреса резались после десятого, а те же
четырнадцать с подменой `X-Real-IP` проходили все. Разбор брал адрес из
заголовка, который пишет отправитель.

Ловушка внутри ловушки: «первый элемент X-Forwarded-For». Прокси дописывает
себя СПРАВА, значит слева лежит то, что прислал клиент.

    PYTHONPATH=. .venv/bin/python -m pytest tests/test_client_ip.py -q
"""
from __future__ import annotations

import pytest

from app.core import rate_limit


class Запрос:
    """Ровно то, что читает разбор: заголовки и адрес сокета."""

    def __init__(self, headers: dict, peer: str | None = "10.0.0.1"):
        self.headers = {k.lower(): v for k, v in headers.items()}
        self.client = type("Сокет", (), {"host": peer})() if peer else None


def test_headers_are_ignored_when_nobody_stands_in_front(monkeypatch):
    """Ноль прокси — заголовкам не верим вовсе.

    Это умолчание, и оно закрытое намеренно: забытая настройка должна
    запирать, а не открывать.
    """
    monkeypatch.setattr("app.config.settings.trusted_proxy_count", 0, raising=False)
    запрос = Запрос({"X-Real-IP": "1.2.3.4", "X-Forwarded-For": "5.6.7.8"})
    assert rate_limit.client_ip(запрос) == "10.0.0.1"


def test_the_proxy_appends_on_the_right(monkeypatch):
    """За одним прокси верим последнему элементу — его дописал он.

    Клиент прислал `9.9.9.9`, ingress дописал `70.70.70.70`. Считать надо
    второе: первое — это то, что клиент про себя сказал сам.
    """
    monkeypatch.setattr("app.config.settings.trusted_proxy_count", 1, raising=False)
    запрос = Запрос({"X-Forwarded-For": "9.9.9.9, 70.70.70.70"})
    assert rate_limit.client_ip(запрос) == "70.70.70.70"


def test_a_forged_chain_cannot_reach_past_our_proxy(monkeypatch):
    """Подделать можно только своё начало цепочки, а читаем мы конец.

    Отправитель шлёт целую цепочку из выдуманных адресов, надеясь, что возьмут
    из неё. Возьмут то, что дописал наш прокси, — то есть его настоящий адрес.
    """
    monkeypatch.setattr("app.config.settings.trusted_proxy_count", 1, raising=False)
    подделка = "1.1.1.1, 2.2.2.2, 3.3.3.3, 4.4.4.4"
    запрос = Запрос({"X-Forwarded-For": f"{подделка}, 70.70.70.70"})
    assert rate_limit.client_ip(запрос) == "70.70.70.70"


def test_a_chain_shorter_than_expected_is_not_trusted(monkeypatch):
    """Цепочка короче, чем прокси, — значит запрос пришёл мимо прокси.

    Верить такому заголовку нельзя: он либо подделан, либо прокси настроен не
    так, как мы думаем, и оба случая означают «не знаю».
    """
    monkeypatch.setattr("app.config.settings.trusted_proxy_count", 2, raising=False)
    запрос = Запрос({"X-Forwarded-For": "9.9.9.9"})
    assert rate_limit.client_ip(запрос) == "10.0.0.1"


def test_x_real_ip_is_not_consulted_at_all(monkeypatch):
    """`X-Real-IP` не читается больше нигде.

    Прежний разбор предпочитал именно его, и именно им снимался счётчик:
    заголовок односоставный, дописать к нему нечего, отличить настоящий от
    присланного невозможно.
    """
    monkeypatch.setattr("app.config.settings.trusted_proxy_count", 1, raising=False)
    запрос = Запрос({"X-Real-IP": "6.6.6.6", "X-Forwarded-For": "9.9.9.9, 70.70.70.70"})
    assert rate_limit.client_ip(запрос) == "70.70.70.70"


def test_the_uvicorn_trapdoor_is_shut():
    """Разбор заголовков у uvicorn обязан быть выключен там, где мы стартуем.

    Он ВКЛЮЧЁН по умолчанию и переписывает `request.client.host` значением из
    `X-Forwarded-For`, если сосед по сокету входит в `--forwarded-allow-ips`
    (по умолчанию 127.0.0.1). То есть адрес сокета, на который опирается разбор
    выше, сам мог прийти из заголовка — и весь этот файл проверял бы вежливую
    фикцию.

    Поймано пробой на живом сервере: при `TRUSTED_PROXY_COUNT=0` счётчик всё
    равно завёлся на подставленном адресе. Проверяется здесь, потому что живёт
    не в коде, а в команде запуска, — и потерять это проще всего.
    """
    from pathlib import Path

    for файл in (Path("Dockerfile"), Path("run-local.sh")):
        текст = файл.read_text()
        строки = [s for s in текст.splitlines()
                  if "uvicorn" in s and "app.main:app" in s and not s.strip().startswith("#")]
        assert строки, f"{файл}: не нашёл строку запуска uvicorn"
        for строка in строки:
            assert "--no-proxy-headers" in строка, (
                f"{файл}: uvicorn стартует без --no-proxy-headers, "
                "значит client.host снова берётся из заголовка"
            )
