"""Что человек видит, когда кадр не вышел.

Раньше приложение получало `status: failed` и ничего больше: ни причины, ни
слова про монеты. Человек видел, что не получилось, и оставался с двумя
вопросами сразу — почему и списали ли с него.

Отдавать внутренний текст было нельзя: в нём имена провайдеров, адреса их
хранилищ и типы исключений. Настоящая строка из журнала выглядела так:

    fal: кадр не скачался за 30 с с v3b.fal.media — хранилище fal отдаёт
    слишком медленно (ReadTimeout)

Это и подсказка тому, кто ищет наши слабые места, и бессмыслица для того, кто
просто хотел картинку. Поэтому наружу уходит перевод, а тест следит за обеими
половинами: что перевод осмысленный и что внутреннее в него не просочилось.

    PYTHONPATH=. .venv/bin/python -m pytest tests/test_failure_text.py -q
"""
from __future__ import annotations

import pytest

from app.routers.generations import failure_text

# То, чего человек не должен увидеть никогда — куски настоящих наших ошибок.
СЕКРЕТНОЕ = (
    "fal", "openrouter", "openai", "gemini", "kling", "pollinations",
    "v3b.fal.media", "queue.fal.run", "readtimeout", "http", "traceback",
    "generationunavailable", "sqlalchemy", "asyncpg",
)

# Настоящие строки, встреченные в журнале.
ИЗ_ЖУРНАЛА = [
    "fal: кадр не скачался за 30 с с v3b.fal.media — хранилище fal отдаёт "
    "слишком медленно (ReadTimeout)",
    "All providers failed for image_to_image: openrouter_gemini_pro: "
    'GenerationUnavailable(\'OpenRouter HTTP 402: {"error":{"message":'
    '"Insufficient credits. Add more using https://openrouter.ai/settings/credits"}}\')',
    "Промпт собрать нечем: перевод недоступен.",
    "fal ответил состоянием ERROR",
    "safety system rejected the request",
    "",
    None,
]


@pytest.mark.parametrize("внутреннее", ИЗ_ЖУРНАЛА)
def test_внутреннее_наружу_не_уходит(внутреннее):
    видное = failure_text(внутреннее).lower()
    for слово in СЕКРЕТНОЕ:
        assert слово not in видное, f"«{слово}» просочилось наружу: {видное}"


@pytest.mark.parametrize("внутреннее", ИЗ_ЖУРНАЛА)
def test_про_монеты_сказано_всегда(внутреннее):
    """Первый вопрос человека — списали ли. Ответ должен быть в каждой строке."""
    assert "TOONTOON" in failure_text(внутреннее)


def test_отказ_модели_отличается_от_отказа_сервиса():
    """Две разные беды — два разных совета, иначе подсказка бесполезна."""
    модель = failure_text("safety system rejected the request")
    сервис = failure_text("All providers failed: ReadTimeout")
    assert модель != сервис
    assert "photo" in модель.lower(), "про снимок надо сказать: его можно заменить"
    assert "again" in сервис.lower(), "про повтор надо сказать: это лечится ожиданием"


def test_незнакомая_беда_не_молчит():
    """Причины может не быть вовсе — фраза всё равно обязана быть человеческой."""
    для_неизвестного = failure_text("нечто, чего мы не предвидели")
    assert для_неизвестного
    assert "TOONTOON" in для_неизвестного


def test_поле_есть_только_у_неудачных():
    """У успешной работы поле молчит, иначе клиенту придётся гадать."""
    from datetime import datetime, timezone
    from app.db import models as m
    from app.routers.generations import _serialize

    def строка(status, error=None):
        row = m.Generation(
            user_id="usr_x", operation="image_to_image", status=status,
            cost=10, request_params={}, error=error)
        row.id = "gen_x"; row.created_at = datetime.now(timezone.utc)
        row.result_media_id = None; row.style_id = None; row.share_id = None
        row.prompt = None
        return _serialize(row)

    assert строка("done")["failure"] is None
    assert строка("running")["failure"] is None
    assert строка("failed", "ReadTimeout")["failure"]
