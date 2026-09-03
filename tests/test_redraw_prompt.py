"""Правка своей работы: меняем одно, остальное как было.

«Тот же кадр, но закат на фоне» к чёрно-белому Spotlight возвращался цветным
пляжем в другой одежде. Виноваты были трое, и каждому здесь по проверке:
сборщик сцены дописывал наряд и волны (его инструкция велела описать
обстановку и настроение); обёртка приклеивала «natural color grading» и
«golden hour»; а фотографический результат считался браком и уходил на
перерисовку с нуля.

    PYTHONPATH=. .venv/bin/python -m pytest tests/test_redraw_prompt.py -q
"""
from __future__ import annotations

import pytest

from app.config import settings
from app.services import gpt
from app.services import prompt_style

pytestmark = pytest.mark.asyncio


def test_инструкция_сборщику_при_правке_своей_работы():
    sys_ = gpt._system_for(editing=True, lettering=False, redraw=True)
    assert "OWN" in sys_ and "EDIT" in sys_
    assert "Do NOT invent a setting" in sys_
    assert "stays black and white" in sys_
    # а обычная правка фотографии — прежняя инструкция про обстановку и наряд
    обычная = gpt._system_for(editing=True, lettering=False, redraw=False)
    assert "the setting, the outfit" in обычная and "OWN" not in обычная


async def test_сборщик_получает_инструкцию_правки_и_обёртка_без_пресета(monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", "test", raising=False)
    увидел = {}
    async def _call(messages, **kw):
        увидел["system"] = messages[0]["content"]; увидел["user"] = messages[-1]["content"]
        return "make the background a warm sunset; the person, the clothing, the pose, the framing, the black and white palette and the lighting stay exactly as they are"
    monkeypatch.setattr(gpt, "_call", _call)
    prompt, _ = await gpt.build_prompt(tile=None, answers={}, free_text="same frame, but make the background a warm sunset instead",
                                       style=None, editing=True, redraw=True)
    assert "OWN" in увидел["system"] and "earlier picture" in " ".join(увидел["system"].split())
    assert prompt.startswith("this picture is the person's own earlier result")
    for чужое in ("natural color grading", "golden hour", "hyperrealistic"):
        assert чужое not in prompt
    assert "warm sunset" in prompt


def test_правка_своей_работы_не_проверяется_на_фотографичность():
    import re, pathlib
    src = pathlib.Path("app/routers/generate.py").read_text()
    m = re.search(r"check_drawn=\((.+?)\)\s*,", src, re.S)
    assert m and "not redraw" in m.group(1)
