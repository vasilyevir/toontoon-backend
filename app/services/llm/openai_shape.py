"""Поставщики, говорящие формой OpenAI: сам OpenAI и витрина OpenRouter.

Провод у них один — `POST /chat/completions` со списком сообщений, — поэтому и
код один, а разного ровно три вещи: адрес, ключ и модель по умолчанию.
"""
from __future__ import annotations

import httpx

from app.config import settings
from app.services.llm.base import Ask, NoAccess, Overloaded, Reply


class ChatCompletionsProvider:
    """Общий провод для всех, кто понимает формат OpenAI."""

    def __init__(self, name: str) -> None:
        self.name = name

    # ── чем этот поставщик отличается от соседа ──────────────────────────────

    @property
    def _is_router(self) -> bool:
        return self.name == "openrouter"

    @property
    def _key(self) -> str:
        return (settings.openrouter_api_key if self._is_router
                else settings.openai_api_key).strip()

    @property
    def _url(self) -> str:
        if self._is_router:
            return f"{settings.openrouter_base_url.rstrip('/')}/chat/completions"
        return "https://api.openai.com/v1/chat/completions"

    def _model(self, asked: str | None) -> str:
        # Просьба о конкретной модели выполнима только через витрину: `asked` —
        # идентификатор вида `google/gemini-2.5-flash`, и прямому OpenAI он не
        # годится, там такой модели нет. Без витрины просьба тихо отменяется:
        # разбор уйдёт на общую модель и сработает хуже, но сработает.
        if self._is_router:
            return asked or settings.openrouter_text_model
        return settings.openai_model

    @property
    def ready(self) -> bool:
        return bool(self._key)

    # ── вызов ────────────────────────────────────────────────────────────────

    async def complete(self, ask: Ask) -> Reply:
        model = self._model(ask.model)
        payload: dict = {
            "model": model,
            "messages": ask.messages,
            "max_tokens": ask.max_tokens,
            "temperature": ask.temperature,
        }
        if self._is_router:
            # Сюда уходят и снимки лиц. Вендор за витриной не должен оставлять
            # их себе; прямой OpenAI такого поля не знает.
            payload["provider"] = {"data_collection": "deny"}
        async with httpx.AsyncClient(timeout=settings.llm_timeout) as client:
            resp = await client.post(
                self._url,
                headers={"Authorization": f"Bearer {self._key}",
                         "Content-Type": "application/json"},
                json=payload)
            _raise_by_meaning(resp, who=self.name)
            body = resp.json()
        return Reply(text=_content_of(body), usage=body.get("usage") or {},
                     model=model, provider=self.name)


def _content_of(payload: dict) -> str:
    """Текст ответа — или пустая строка, если его нет.

    Пустой ответ приходит штатно: модели с рассуждением возвращают
    `content: null`, потратив весь лимит на размышление. Это не сбой сети, а
    «ничего не сказал», и звать у пустоты `.strip()` здесь уже случалось —
    разбор фразы отвечал приложению пятисоткой.
    """
    choices = payload.get("choices") or [{}]
    message = choices[0].get("message") or {}
    return (message.get("content") or "").strip()


def _raise_by_meaning(resp: httpx.Response, *, who: str) -> None:
    if resp.is_success:
        return
    if resp.status_code in (401, 402, 403):
        raise NoAccess(f"{who}: HTTP {resp.status_code}")
    if resp.status_code == 429 or resp.status_code >= 500:
        raise Overloaded(f"{who}: HTTP {resp.status_code}")
    resp.raise_for_status()
