"""Когда ключ основного провайдера мёртв, текст идёт через fal.

Ключ OpenRouter истёк, счёт OpenAI опустел — и приложение осталось без
подсказок, без разбора просьб словами и без названий, хотя оплаченный ключ
fal лежал рядом (Илья, 2026-09-15). fal своих языковых моделей не держит:
`openrouter/router` проксирует тот же OpenRouter, только платим мы кредитами
fal. Картинки туда не уходят — у этого входа их просто нет.
"""
from __future__ import annotations

import httpx
import pytest

from app.config import settings
from app.services import gpt


class _Response:
    def __init__(self, status: int, payload: dict):
        self.status_code = status
        self._payload = payload

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "denied", request=httpx.Request("POST", "https://x"),
                response=httpx.Response(self.status_code))


@pytest.fixture(autouse=True)
def _keys(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "dead-key", raising=False)
    monkeypatch.setattr(settings, "fal_api_key", "fal-key", raising=False)
    monkeypatch.setattr(settings, "fal_text_fallback", True, raising=False)


def _client(handler):
    class _Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *_): return False
        async def post(self, url, headers=None, json=None): return handler(url, headers, json)
    return lambda *a, **k: _Client()


@pytest.mark.asyncio
async def test_expired_key_falls_back_to_fal(monkeypatch) -> None:
    seen: list[str] = []

    def handler(url, headers, json):
        seen.append(url)
        if "fal.run" in url:
            assert headers["Authorization"].startswith("Key ")
            # У fal нет ролей: система и просьба приезжают двумя строками.
            assert json["system_prompt"] == "be terse"
            assert json["prompt"] == "ideas?"
            return _Response(200, {"output": "one\ntwo", "error": None})
        return _Response(401, {})

    monkeypatch.setattr(httpx, "AsyncClient", _client(handler))
    answer = await gpt._call(
        [{"role": "system", "content": "be terse"},
         {"role": "user", "content": "ideas?"}], max_tokens=50)
    assert answer == "one\ntwo"
    assert any("fal.run" in url for url in seen)


@pytest.mark.asyncio
async def test_pictures_are_not_sent_to_fal(monkeypatch) -> None:
    def handler(url, headers, json):
        assert "fal.run" not in url, "снимок не должен уходить туда, где его не примут"
        return _Response(401, {})

    monkeypatch.setattr(httpx, "AsyncClient", _client(handler))
    with pytest.raises(httpx.HTTPStatusError):
        await gpt._call([{"role": "user", "content": [{"type": "image_url"}]}], max_tokens=10)


@pytest.mark.asyncio
async def test_fallback_can_be_switched_off(monkeypatch) -> None:
    monkeypatch.setattr(settings, "fal_text_fallback", False, raising=False)
    monkeypatch.setattr(httpx, "AsyncClient", _client(lambda *_: _Response(402, {})))
    with pytest.raises(httpx.HTTPStatusError):
        await gpt._call([{"role": "user", "content": "hi"}], max_tokens=10)


# ─── Разбор строк подсказок ──────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("«me»: «Make a watercolor portrait of me».", "Make a watercolor portrait of me"),
    ("1. me: Turn me into a hero", "Turn me into a hero"),
    ('- "Put me on a rooftop at sunset"', "Put me on a rooftop at sunset"),
    ("Design a poster of me", "Design a poster of me"),
    # Двоеточие внутри самой мысли — не ярлык, и трогать его нельзя.
    ("A cat: the story of my life", "A cat: the story of my life"),
])
def test_idea_lines_lose_only_decoration(raw: str, expected: str) -> None:
    """Модели обрамляют строку нумерацией, кавычками и подписью «me:».

    Через fal это особенно заметно: у него нет ролей, система и просьба
    склеиваются в один текст, и часть моделей отвечает репликой с ярлыком.
    """
    assert gpt._bare_idea(raw) == expected
