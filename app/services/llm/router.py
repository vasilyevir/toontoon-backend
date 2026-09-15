"""Кому задать вопрос и что делать, когда он не ответил.

Поставщиков несколько, и это не роскошь: за полгода у нас по очереди умерли
ключ OpenRouter и счёт OpenAI, каждый раз унося с собой подсказки, разбор
просьб и проверку содержимого. Порядок задаётся настройкой `llm_order`, первым
сейчас стоит fal.

Разница между «не ответил» и «отказал» здесь главная. Перегрузку и обрыв связи
имеет смысл пересдать тому же поставщику: это мгновенные отказы, и вторая
попытка почти ничего не стоит. Истёкший ключ и пустой счёт пересдавать
бессмысленно — сразу к следующему кошельку. А кривой запрос (400) не чинится
ни повтором, ни сменой поставщика, и второй платный вызов ему не поможет —
такая ошибка летит наверх сразу.
"""
from __future__ import annotations

import asyncio
import logging

import httpx

from app.config import settings
from app.services.llm.base import Ask, NoAccess, Overloaded, Reply
from app.services.llm.fal import FalProvider
from app.services.llm.openai_shape import ChatCompletionsProvider

log = logging.getLogger(__name__)

_RETRY_PAUSE_SECONDS = 1.0

_KNOWN: dict[str, object] = {
    "fal": FalProvider(),
    "openrouter": ChatCompletionsProvider("openrouter"),
    "openai": ChatCompletionsProvider("openai"),
}


class NoProvider(RuntimeError):
    """Ни одного ключа: спросить некого."""


def chain() -> list:
    """Поставщики в порядке очереди — только те, у кого есть ключ."""
    order = [name.strip() for name in settings.llm_order.split(",") if name.strip()]
    return [_KNOWN[name] for name in order if name in _KNOWN and _KNOWN[name].ready]


def enabled() -> bool:
    """Есть ли кому отвечать. Проверка перед работой, чтобы не звать зря."""
    return bool(chain())


async def ask(request: Ask) -> Reply:
    """Ответ первого поставщика, который смог."""
    providers = chain()
    if not providers:
        raise NoProvider("нет ни одного ключа языковой модели")
    trouble: Exception | None = None
    for provider in providers:
        for attempt in (1, 2):
            try:
                return await provider.complete(request)
            except NoAccess as error:
                # Ключ истёк или счёт пуст — тем же ключом лучше не станет.
                log.warning("%s не пускает (%s) — следующий поставщик",
                            provider.name, error)
                trouble = error
                break
            except (Overloaded, httpx.ConnectError, httpx.ReadError,
                    httpx.RemoteProtocolError) as error:
                trouble = error
                if attempt == 2:
                    log.warning("%s не отвечает (%r) — следующий поставщик",
                                provider.name, error)
                    break
                log.warning("%s: %r — повтор", provider.name, error)
                await asyncio.sleep(_RETRY_PAUSE_SECONDS)
            except httpx.TimeoutException as error:
                # Таймаут не пересдаём: он уже съел свои секунды, и второй
                # заход рискует упереться в таймаут прокси, оставив человека
                # вообще без ответа. Зато другой поставщик может быть быстрее.
                log.warning("%s молчит дольше срока — следующий поставщик",
                            provider.name)
                trouble = error
                break
    raise trouble or NoProvider("никто не ответил")
