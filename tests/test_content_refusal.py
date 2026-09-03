"""Отказ по содержанию останавливает цепочку исполнителей.

Фолбэк создавался для квоты, 5xx и плохой минуты у вендора — там обойти
отказ правильно. Отказ по содержанию — не поломка, а решение; следующий в
очереди может не проверять вовсе, и «попробовать его» значит снять чужую
модерацию нашими руками. Живой пример: OpenAI отклонил «сделай меня как Том
Круз», nano-banana — нарисовал.

    PYTHONPATH=. .venv/bin/python -m pytest tests/test_content_refusal.py -q
"""
from __future__ import annotations

import types

import pytest

from app.routers.generations import failure_text
from app.services.generation.operations import ContentRefused, GenerationUnavailable
from app.services.generation import registry

pytestmark = pytest.mark.asyncio


class Строка:
    def __init__(self, id, model): self.id, self.model = id, model


async def test_отказ_по_содержанию_не_идёт_к_следующему(monkeypatch):
    вызваны = []
    async def попытка(provider_id, adapter, request, model):
        вызваны.append(provider_id)
        if provider_id == "первый":
            raise ContentRefused("fal: модель не взялась за снимок, HTTP 422: content_policy_violation")
        return types.SimpleNamespace(data=b"frame")
    monkeypatch.setattr(registry, "_attempt", попытка)
    with pytest.raises(ContentRefused):
        await registry._run_through([(Строка("первый", "m1"), object()), (Строка("второй", "m2"), object())],
                                    request=_просьба())
    assert вызваны == ["первый"], "второго звать нельзя — он не проверяет содержимое"


async def test_обычный_отказ_по_прежнему_обходится(monkeypatch):
    вызваны = []
    async def попытка(provider_id, adapter, request, model):
        вызваны.append(provider_id)
        if provider_id == "первый":
            raise GenerationUnavailable("fal HTTP 503")
        return types.SimpleNamespace(data=b"frame")
    monkeypatch.setattr(registry, "_attempt", попытка)
    assert (await registry._run_through([(Строка("первый", "m1"), object()), (Строка("второй", "m2"), object())],
                                       request=_просьба())).data == b"frame"
    assert вызваны == ["первый", "второй"]


def test_человек_читает_про_снимок_а_не_про_сервис():
    """Настоящий текст fal при отказе по содержанию — совет сменить фото, не «попробуйте позже»."""
    текст = failure_text("fal: модель не взялась за снимок, HTTP 422: "
                         '{"detail":[{"msg":"flagged by a content checker","type":"content_policy_violation"}]}')
    assert "photo" in текст.lower() and "TOONTOON" in текст


def _просьба():
    from app.services.generation.operations import GenerationRequest, Operation
    return GenerationRequest(operation=Operation.IMAGE_TO_IMAGE, prompt="x", image=b"f", image_mime="image/jpeg")


def test_счётчик_отказов_узнаёт_ContentRefused():
    """Суточный бан для упорных считает отказы провайдера по тексту ошибки.

    Раньше до `image_job` доезжала обёртка «All providers failed: …», и в ней
    встречалось `content_policy_violation`. Теперь доезжает сам `ContentRefused`
    — его текст обязан узнаваться так же, иначе бан молча перестанет работать.
    """
    from app.services import policy
    e = ContentRefused("fal: модель не взялась за снимок, HTTP 422: "
                       '{"detail":[{"type":"content_policy_violation"}]}')
    assert policy.looks_like_moderation(repr(e))


def test_правка_своей_работы_не_идёт_на_перерисовку_как_брак():
    """Носитель при правке задаёт исходник, а не стиль, угаданный по тексту.

    «Тот же кадр, но закат на фоне» к фотографическому Spotlight: чат подобрал
    рисованный стиль, результат вернулся фотографией — и был перерисован с нуля
    как брак. В коде это выражено одной связкой: проверка на «а не фото ли»
    выключена, когда правим свою работу.
    """
    import re, pathlib
    src = pathlib.Path("app/routers/generate.py").read_text()
    m = re.search(r"^\s*check_drawn\s*=\s*(.+)$", src, re.M)
    assert m and "and not redraw" in m.group(1), m.group(1) if m else "нет check_drawn"
