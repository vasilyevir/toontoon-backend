"""fal как поставщик языковых моделей.

Своих языковых моделей у fal нет: `openrouter/router` проксирует тот же
OpenRouter, только платим мы кредитами fal. Ценно это тем, что кошелёк другой —
ключ OpenRouter однажды истёк, счёт OpenAI опустел, и приложение осталось без
подсказок, хотя рабочий ключ fal лежал рядом.

Вход у fal не такой, как у OpenAI. Ролей нет вовсе: есть `system_prompt` —
одна строка, и `prompt` — тоже одна. Картинки идут не внутри сообщения, а
отдельным списком `image_urls`, и для них отдельный адрес — `…/router/vision`.
Поэтому здесь живёт разговорная стенограмма: переписка складывается в одну
строку с подписями, кто что сказал, и заканчивается репликой-приглашением,
чтобы модель продолжила за ассистента, а не пересказала диалог.
"""
from __future__ import annotations

import httpx

from app.config import settings
from app.services.llm.base import Ask, NoAccess, Overloaded, Reply

#: Подписи в стенограмме. Английские: разговор с моделью идёт по-английски,
#: даже когда человек пишет по-русски.
_PERSON = "User"
_MODEL = "Assistant"


def transcript(messages: list[dict]) -> tuple[str, str, list[str]]:
    """Свести переписку к тому, что понимает fal.

    Возвращает «система, просьба, картинки».

    Три правила, и каждое взято из того, как этим пользуются у нас:

    1. Вступительные системные реплики — в `system_prompt`. Это роль модели, и
       ей место над разговором.
    2. Системная реплика в середине или в конце разговора остаётся на своём
       месте строкой-указанием. У нас так написан чат: указание, о чём спросить,
       стоит после истории именно потому, что модель тем сильнее слушает, чем
       ближе к концу написано. Подняв его наверх, мы бы сломали ровно то, ради
       чего его туда опустили.
    3. Один-единственный вопрос остаётся голым текстом, без подписей. Почти все
       наши задачи — разбор фразы, название, короткая подсказка — это один
       вопрос, и подпись «User:» перед ним модель охотно копирует в ответ.
    """
    system: list[str] = []
    body: list[dict] = []
    for message in messages:
        if message.get("role") == "system" and not body:
            system.append(_text_of(message))
        else:
            body.append(message)

    images: list[str] = []
    for message in body:
        images.extend(_images_of(message))

    if len(body) == 1 and body[0].get("role") != "system":
        return "\n\n".join(system), _text_of(body[0]), images

    lines: list[str] = []
    for message in body:
        role = message.get("role")
        text = _text_of(message)
        if not text:
            continue
        if role == "system":
            lines.append(f"[Instruction to {_MODEL}: {text}]")
        else:
            lines.append(f"{_MODEL if role == 'assistant' else _PERSON}: {text}")
    # Приглашение продолжить: без него модель дописывает за человека или
    # подводит итог разговора вместо ответа в нём.
    lines.append(f"{_MODEL}:")
    return "\n\n".join(system), "\n".join(lines), images


def _text_of(message: dict) -> str:
    """Слова сообщения — включая сообщения из частей, где есть картинки."""
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    words = [(part or {}).get("text", "") for part in (content or [])
             if (part or {}).get("type") == "text"]
    return "\n".join(w for w in words if w).strip()


def _images_of(message: dict) -> list[str]:
    content = message.get("content")
    if isinstance(content, str):
        return []
    found = []
    for part in content or []:
        url = ((part or {}).get("image_url") or {}).get("url")
        if url:
            found.append(url)
    return found


class FalProvider:
    """Текст и зрение через fal."""

    name = "fal"

    @property
    def ready(self) -> bool:
        return bool(settings.fal_api_key.strip())

    async def complete(self, ask: Ask) -> Reply:
        system, prompt, images = transcript(ask.messages)
        model = ask.model if (ask.model and "/" in ask.model) else settings.fal_text_model
        payload = {
            "model": model,
            # Пустая просьба у fal — отказ разбора; такого у нас быть не должно,
            # но пусть лучше модель ответит ни на что, чем запрос упадёт.
            "prompt": prompt or "Continue.",
            "system_prompt": system,
            "max_tokens": ask.max_tokens,
            "temperature": ask.temperature,
        }
        url = settings.fal_text_url
        if images:
            payload["image_urls"] = images
            url = settings.fal_vision_url
        async with httpx.AsyncClient(timeout=settings.llm_timeout) as client:
            resp = await client.post(
                url,
                headers={"Authorization": f"Key {settings.fal_api_key.strip()}",
                         "Content-Type": "application/json"},
                json=payload)
            _raise_by_meaning(resp)
            body = resp.json()
        if body.get("error"):
            raise RuntimeError(str(body["error"]))
        return Reply(text=str(body.get("output") or ""),
                     usage=body.get("usage") or {}, model=model,
                     provider=self.name)


def _raise_by_meaning(resp: httpx.Response) -> None:
    """Перевести отказ fal на язык, понятный вызывающей стороне."""
    if resp.is_success:
        return
    if resp.status_code in (401, 402, 403):
        raise NoAccess(f"fal: HTTP {resp.status_code}")
    if resp.status_code == 429 or resp.status_code >= 500:
        raise Overloaded(f"fal: HTTP {resp.status_code}")
    resp.raise_for_status()
