"""События о судьбе работы: что уходит в Amplitude и с какими полями.

Половину воронки шлёт сервер — «принял», «сделал», «не вышло», — и склеивается
она с клиентской половиной по трём вещам: имени события, `generation_id` и
User ID. Ошибиться в любой из трёх значит получить в отчёте две разные воронки
вместо одной, причём молча: Amplitude примет что угодно.

Отсюда три группы проверок. Что код отказа короткий и не тащит наружу
внутренний текст — тот же разбор, что у человеческих формулировок, но для
другого читателя. Что источник снимка берётся у приложения, а когда оно молчит
— выводится по профилю. Что без ключа не уходит ничего.

    PYTHONPATH=. .venv/bin/python -m pytest tests/test_product_events.py -q
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.config import settings
from app.services import product_events


def работа(**поля):
    основа = dict(id="gen_1", user_id="usr_1", style_id="neon_rain",
                  request_params={}, error=None)
    основа.update(поля)
    return SimpleNamespace(**основа)


# ── Код отказа ───────────────────────────────────────────────────────────────

# Настоящие строки из журнала → код, который увидит аналитик.
ИЗ_ЖУРНАЛА = [
    ('fal: модель не взялась за снимок, HTTP 422: {"detail":[{"msg":"The content '
     'could not be processed because it contained material flagged by a content '
     'checker."}]}', "content_policy"),
    ("fal: кадр не скачался за 30 с с v3b.fal.media — хранилище fal отдаёт "
     "слишком медленно (ReadTimeout)", "provider_timeout"),
    ("Промпт собрать нечем: перевод недоступен.", "prompt_unavailable"),
    ('All providers failed for image_to_image: openrouter_gemini_pro: '
     'GenerationUnavailable(\'OpenRouter HTTP 402: Insufficient credits\')',
     "insufficient_funds"),
    ("Работа оборвалась: процесс не дожил до конца (найдена сверкой).", "stalled"),
    ("fal ответил состоянием ERROR", "unknown_error"),
]


@pytest.mark.parametrize("текст,код", ИЗ_ЖУРНАЛА)
def test_код_отказа_короткий_и_ожидаемый(текст, код):
    assert product_events.error_code(текст) == код


def test_код_не_тащит_наружу_внутренности():
    """Код — это слово из нашего словаря, а не кусок ошибки.

    Внутренний текст пестрит именами провайдеров и адресами их хранилищ. В
    человеческую формулировку он не попадает по той же причине, и здесь проверка
    строже: код обязан быть одним из перечисленных, других значений нет.
    """
    словарь = {код for _, код in ИЗ_ЖУРНАЛА} | {
        "provider_unavailable", "cancelled"}
    for текст, _ in ИЗ_ЖУРНАЛА:
        assert product_events.error_code(текст) in словарь
    assert product_events.error_code(None) == "unknown_error"


# ── Источник снимка ──────────────────────────────────────────────────────────

def test_источник_берётся_у_приложения():
    """Сказанное приложением сильнее любых догадок."""
    assert product_events.photo_source({"photo_source": "camera"}) == "camera"
    # Даже когда профиль тоже есть: человек мог приложить снимок при заведённом
    # профиле, и источник здесь — снимок.
    assert product_events.photo_source(
        {"photo_source": "photo_library", "profile_id": "p1"}) == "photo_library"


def test_молчание_приложения_выводится_по_профилю():
    """Кадр из чата и старые сборки поля не шлют."""
    assert product_events.photo_source({"profile_id": "p1"}) == "ai_profile"
    assert product_events.photo_source({"profile_ids": ["p1", "p2"]}) == "ai_profile"
    assert product_events.photo_source({}) == "unknown"
    assert product_events.photo_source(None) == "unknown"


# ── Отправка ─────────────────────────────────────────────────────────────────

@pytest.fixture
def перехват(monkeypatch):
    """Ключ на месте, сеть заменена: смотрим, что именно ушло бы."""
    ушло: list[tuple[str, str, dict]] = []

    async def подмена(user_id, event, props):
        ушло.append((user_id, event, props))

    monkeypatch.setattr(settings, "amplitude_api_key", "key-for-tests")
    monkeypatch.setattr(product_events, "_send", подмена)
    return ушло


async def test_принятая_задача(перехват):
    product_events.started(работа(request_params={"photo_source": "camera"}))
    await _дождаться()
    (user_id, событие, поля), = перехват
    assert user_id == "usr_1"
    assert событие == "generation_started"
    assert поля == {"template_id": "neon_rain", "photo_source": "camera",
                    "generation_id": "gen_1"}


async def test_готовая_работа(перехват):
    product_events.completed(работа(request_params={"profile_id": "p1"}))
    await _дождаться()
    _, событие, поля = перехват[0]
    assert событие == "generation_completed"
    assert поля["photo_source"] == "ai_profile"


async def test_неудача_несёт_код_а_не_текст(перехват):
    внутренний = ("fal: кадр не скачался за 30 с с v3b.fal.media (ReadTimeout)")
    product_events.failed(работа(error=внутренний), внутренний)
    await _дождаться()
    _, событие, поля = перехват[0]
    assert событие == "generation_failed"
    assert поля["generation_error_code"] == "provider_timeout"
    # Имени провайдера и адреса хранилища в событии нет ни в одном поле.
    assert "fal" not in " ".join(str(з) for з in поля.values()).lower()


async def test_шаблон_из_параметров_когда_колонка_пуста(перехват):
    """Стиль из `content/` внешним ключом не защищён и в колонку не попадает.

    История уже наступала на это: без такого запасного пути работа показывалась
    серым квадратом. Событие точно так же осталось бы без шаблона.
    """
    product_events.started(работа(style_id=None,
                                  request_params={"style_id": "gallery_noir"}))
    await _дождаться()
    _, _, поля = перехват[0]
    assert поля["template_id"] == "gallery_noir"


async def test_без_ключа_не_уходит_ничего(monkeypatch):
    ушло = []

    async def подмена(*args):
        ушло.append(args)

    monkeypatch.setattr(settings, "amplitude_api_key", "")
    monkeypatch.setattr(product_events, "_send", подмена)
    product_events.started(работа())
    product_events.completed(работа())
    product_events.failed(работа(), "любая ошибка")
    await _дождаться()
    assert ушло == []
    assert not product_events.enabled()


async def test_без_человека_не_уходит_ничего(перехват):
    """Событие без User ID не склеится с клиентской половиной — и не нужно."""
    product_events.started(работа(user_id=None))
    await _дождаться()
    assert перехват == []


async def _дождаться():
    """Отправка отпущена в фон — дать задачам добежать."""
    import asyncio
    while product_events._летят:
        await asyncio.gather(*list(product_events._летят), return_exceptions=True)
