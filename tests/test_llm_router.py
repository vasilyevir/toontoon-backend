"""Очередь поставщиков языковых моделей и разговор в форме, понятной fal.

Ключ OpenRouter истёк, счёт OpenAI опустел — и приложение осталось без
подсказок, без разбора просьб словами и без проверки содержимого, хотя
оплаченный ключ fal лежал рядом (Илья, 2026-09-15). Отсюда две вещи, которые
здесь проверяются: очередь кошельков и стенограмма — у fal нет ролей, и
переписку приходится складывать в одну строку.

    PYTHONPATH=. .venv/bin/python -m pytest tests -q
"""
from __future__ import annotations

import httpx
import pytest

from app.config import settings
from app.services import gpt
from app.services.llm import fal, router


@pytest.fixture(autouse=True)
def keys(monkeypatch):
    monkeypatch.setattr(settings, "llm_order", "fal,openrouter,openai")
    monkeypatch.setattr(settings, "fal_api_key", "fal-key")
    monkeypatch.setattr(settings, "openrouter_api_key", "router-key")
    monkeypatch.setattr(settings, "openai_api_key", "openai-key")


@pytest.fixture(autouse=True)
def no_retry_pause(monkeypatch):
    async def _instant(_seconds):
        return None

    monkeypatch.setattr(router.asyncio, "sleep", _instant)


def _post(monkeypatch, handler):
    """Подменить сеть: handler(url, headers, json) → httpx.Response."""
    seen: list[tuple[str, dict]] = []

    async def _do(self, url, headers=None, json=None, **_):
        seen.append((url, json or {}))
        return handler(url, headers or {}, json or {})

    monkeypatch.setattr(httpx.AsyncClient, "post", _do)
    return seen


def _fal_says(text: str) -> httpx.Response:
    return httpx.Response(200, json={"output": text, "usage": {"cost": 0.0004}},
                          request=httpx.Request("POST", "https://fal.run/x"))


def _openai_says(text: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": text}}]},
                          request=httpx.Request("POST", "https://openrouter.ai/x"))


def _refuses(code: int) -> httpx.Response:
    return httpx.Response(code, request=httpx.Request("POST", "https://x/y"))


# ─── Очередь ─────────────────────────────────────────────────────────────────

async def test_text_goes_to_fal_first(monkeypatch) -> None:
    """fal стоит первым, и слова уходят на его текстовый вход."""
    def handler(url, headers, body):
        assert url == settings.fal_text_url
        assert headers["Authorization"].startswith("Key ")
        # У fal нет ролей: система и просьба приезжают двумя строками.
        assert body["system_prompt"] == "be terse"
        assert body["prompt"] == "ideas?"
        return _fal_says("one\ntwo")

    _post(monkeypatch, handler)
    answer = await gpt._call([{"role": "system", "content": "be terse"},
                              {"role": "user", "content": "ideas?"}], max_tokens=50)
    assert answer == "one\ntwo"


async def test_photo_goes_to_the_vision_entrance(monkeypatch) -> None:
    """Снимок — другой адрес и отдельный список картинок, а не часть сообщения."""
    def handler(url, headers, body):
        assert url == settings.fal_vision_url
        assert body["image_urls"] == ["data:image/jpeg;base64,AAA"]
        assert body["prompt"] == "what is on it?"
        return _fal_says("a drawing")

    _post(monkeypatch, handler)
    answer = await gpt._call([{"role": "user", "content": [
        {"type": "text", "text": "what is on it?"},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAA"}}]}])
    assert answer == "a drawing"


async def test_dead_key_moves_on_to_the_next_wallet(monkeypatch) -> None:
    """Истёкший ключ не пересдают — берут следующий кошелёк."""
    def handler(url, headers, body):
        if "fal.run" in url:
            return _refuses(401)
        assert "openrouter.ai" in url
        # У витрины формат OpenAI: роли на месте.
        assert body["messages"][0]["role"] == "system"
        return _openai_says("a scene")

    seen = _post(monkeypatch, handler)
    assert await gpt._call([{"role": "system", "content": "s"},
                            {"role": "user", "content": "hi"}]) == "a scene"
    assert len(seen) == 2, "мёртвый ключ пересдавать незачем"


async def test_overload_is_retried_before_the_next_wallet(monkeypatch) -> None:
    answers = [_refuses(503), _refuses(503), _openai_says("a scene")]

    def handler(url, headers, body):
        return answers.pop(0)

    seen = _post(monkeypatch, handler)
    assert await gpt._call([{"role": "user", "content": "hi"}]) == "a scene"
    assert [url for url, _ in seen][:2] == [settings.fal_text_url] * 2


async def test_order_is_a_setting(monkeypatch) -> None:
    """Порядок задаётся настройкой, и поставщик без ключа в очередь не встаёт."""
    monkeypatch.setattr(settings, "llm_order", "openai,fal")
    monkeypatch.setattr(settings, "openai_api_key", "")
    assert [p.name for p in router.chain()] == ["fal"]


async def test_without_keys_nobody_is_called(monkeypatch) -> None:
    monkeypatch.setattr(settings, "fal_api_key", "")
    monkeypatch.setattr(settings, "openrouter_api_key", "")
    monkeypatch.setattr(settings, "openai_api_key", "")
    assert not router.enabled()
    with pytest.raises(router.NoProvider):
        await gpt._call([{"role": "user", "content": "hi"}])


async def test_analytics_names_the_wallet_that_paid(monkeypatch) -> None:
    """В аналитику едет тот, кто ответил, а не догадка по имени модели.

    Имя `google/gemini-2.5-flash` со слэшем выглядит как витрина, но через fal
    за него платим мы кредитами fal — и в замере расходов это разные строки.
    """
    seen: dict = {}

    def remember(**kwargs):
        seen.update(kwargs)

    from app.services import agent_analytics
    monkeypatch.setattr(agent_analytics, "model_answered", remember)
    _post(monkeypatch, lambda *_: _fal_says("ok"))

    await gpt._call([{"role": "user", "content": "hi"}],
                    model="google/gemini-2.5-flash")
    assert seen["provider"] == "fal"
    assert seen["model"] == "gemini-2.5-flash"


# ─── Стенограмма разговора ───────────────────────────────────────────────────

def test_a_single_question_stays_bare() -> None:
    """Один вопрос — голый текст: подпись «User:» модель копирует в ответ."""
    system, prompt, images = fal.transcript(
        [{"role": "system", "content": "you are Toontoon"},
         {"role": "user", "content": "make me a hero"}])
    assert system == "you are Toontoon"
    assert prompt == "make me a hero"
    assert images == []


def test_a_conversation_keeps_who_said_what() -> None:
    """Многоходовой разговор складывается в стенограмму с приглашением ответить."""
    _, prompt, _ = fal.transcript([
        {"role": "system", "content": "you are Toontoon"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello!"},
        {"role": "user", "content": "a poster please"},
    ])
    assert prompt.splitlines() == [
        "User: hi", "Assistant: hello!", "User: a poster please", "Assistant:"]


def test_a_late_instruction_stays_late() -> None:
    """Указание после истории остаётся после неё.

    В чате оно стоит там не случайно: модель тем сильнее слушает, чем ближе к
    концу написано, и подняв его наверх мы сломали бы ровно то, ради чего его
    туда опустили.
    """
    system, prompt, _ = fal.transcript([
        {"role": "system", "content": "you are Toontoon"},
        {"role": "user", "content": "hi"},
        {"role": "system", "content": "ask about the format"},
        {"role": "user", "content": "ok"},
    ])
    assert system == "you are Toontoon"
    assert "[Instruction to Assistant: ask about the format]" in prompt
    assert prompt.index("User: hi") < prompt.index("[Instruction")


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

    Через fal это особенно заметно: ролей нет, система и просьба склеиваются в
    один текст, и часть моделей отвечает репликой с ярлыком.
    """
    assert gpt._bare_idea(raw) == expected
