"""Общий язык для всех поставщиков языковых моделей.

Приложение спрашивает модель в сорока местах: промпты, подсказки, разбор
просьбы словами, зрение на снимке, политика. Все они разговаривают формой
OpenAI — список сообщений с ролями. Поставщики же разные: у одного тот же
формат, у другого (fal) ролей нет вовсе, зато есть отдельный вход для
картинок. Этот слой держит разницу в одном месте: выше остаётся один вызов,
ниже — по файлу на поставщика.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol


class NoAccess(Exception):
    """Ключ истёк, счёт пуст или доступ закрыт. Повтор не поможет — нужен
    другой поставщик."""


class Overloaded(Exception):
    """Слишком часто или временно недоступно. Имеет смысл пересдать."""


@dataclass(frozen=True)
class Ask:
    """Один вопрос к модели в привычной форме OpenAI."""

    messages: list[dict]
    max_tokens: int = 300
    temperature: float = 0.7
    #: Имя модели в каталоге OpenRouter («google/gemini-2.5-flash»). Пусто —
    #: поставщик берёт свою по умолчанию.
    model: Optional[str] = None
    #: Зачем спрашиваем — только для аналитики.
    purpose: Optional[str] = None

    @property
    def images(self) -> list[str]:
        """Картинки из сообщений, в порядке появления.

        Зрение приезжает тем же списком сообщений, где `content` — не строка,
        а части с `image_url`. Поставщику важно знать это заранее: у fal для
        картинок отдельный вход.
        """
        found: list[str] = []
        for message in self.messages:
            content = message.get("content")
            if isinstance(content, list):
                for part in content:
                    url = (part or {}).get("image_url", {}).get("url")
                    if url:
                        found.append(url)
        return found


@dataclass
class Reply:
    text: str
    usage: dict = field(default_factory=dict)
    #: Модель, которая в итоге ответила, — для аналитики и счёта.
    model: str = ""
    #: Чей кошелёк за неё заплатил: fal, openrouter или openai.
    provider: str = ""


class Provider(Protocol):
    """Поставщик модели."""

    name: str

    @property
    def ready(self) -> bool:
        """Есть ключ и поставщика можно звать."""

    async def complete(self, ask: Ask) -> Reply:
        """Ответ модели. Бросает `NoAccess` / `Overloaded` по смыслу отказа."""
