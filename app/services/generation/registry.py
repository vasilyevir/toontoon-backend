"""Which model performs an operation — data, not code.

The adapter lives in Python, the switch lives in the database. Turning a model
on, changing the order, falling back after an outage, running two models side by
side — all of that is a row in ``generation_providers``, not a release (CH-21).
"""
from __future__ import annotations

import logging
import time
from typing import Optional, Sequence

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import models as m
from app.services.generation.operations import (
    GenerationRequest,
    GenerationResult,
    GenerationUnavailable,
    Operation,
)
from app.services.generation.providers.base import Provider
from app.services.generation.providers.fal import FalProvider
from app.services.generation.providers.openai_images import OpenAIImagesProvider
from app.services.generation.providers.openrouter import OpenRouterProvider

logger = logging.getLogger("toontoon.generation")

# Every adapter we ship. Presence here does not enable anything: a provider is
# used only if the database says so.
ADAPTERS: dict[str, Provider] = {
    p.id: p
    for p in (
        OpenAIImagesProvider(),
    )
}

# За этим префиксом стоит не вендор, а витрина: один ключ, один эндпоинт и
# четыре десятка моделей. Перечислять их здесь поимённо значило бы возвращать
# релиз туда, откуда его убрал реестр, — поэтому строка `openrouter_*` получает
# адаптер сама, а какая за ней модель, написано в колонке `model`.
_OPENROUTER_PREFIX = "openrouter"

# И то же самое для fal: тоже витрина, тоже один ключ на семейства моделей.
# Строка `fal_nano`, `fal_flux`, `fal_seedream` получает адаптер сама.
_FAL_PREFIX = "fal"


def adapter_for(provider_id: str) -> Optional[Provider]:
    adapter = ADAPTERS.get(provider_id)
    if adapter is not None:
        return adapter
    if provider_id == _OPENROUTER_PREFIX or provider_id.startswith(f"{_OPENROUTER_PREFIX}_"):
        return OpenRouterProvider(provider_id)
    if provider_id == _FAL_PREFIX or provider_id.startswith(f"{_FAL_PREFIX}_"):
        return FalProvider(provider_id)
    return None


# Сколько референсных снимков принимает провайдер, если строка молчит.
# Единица, а не ноль: однолицый путь — основной, и старые строки, заведённые
# до многосубъектных кадров, должны продолжать работать без правки.
DEFAULT_MAX_REFERENCES = 1


async def candidates(
    session: AsyncSession, operation: Operation, *, references: int = 1
) -> list[tuple[m.GenerationProvider, Provider]]:
    """Enabled providers for an operation, best first.

    `references` отсеивает тех, кто не возьмёт столько снимков. Для пары нужны
    двое узнаваемых в кадре, и провайдер с одним референсом сделает вид, что
    справился: вернёт кадр с одним человеком и незнакомцем рядом. Отказ на входе
    честнее — реестр уйдёт к тому, кто умеет.

    Предел объявлен в `limits` строки, а не в коде адаптера: у витрины за одним
    адаптером стоят модели с разным пределом — Gemini берёт четырнадцать
    снимков, Recraft один, — и это свойство строки, а не класса.
    """
    stmt = (
        select(m.GenerationProvider)
        .where(m.GenerationProvider.is_enabled.is_(True))
        .order_by(m.GenerationProvider.priority)
    )
    rows = (await session.scalars(stmt)).all()

    result = []
    for row in rows:
        if operation.value not in (row.operations or []):
            continue
        adapter = adapter_for(row.id)
        if adapter is None:
            # A registry row with no adapter is a configuration mistake, not a
            # reason to fail a user's request — say so and move on.
            logger.warning("No adapter for provider %r", row.id)
            continue
        if operation not in adapter.operations:
            logger.warning(
                "Provider %r is registered for %s but cannot do it", row.id, operation.value
            )
            continue
        if not adapter.available():
            continue
        if (row.limits or {}).get("max_references", DEFAULT_MAX_REFERENCES) < references:
            continue
        result.append((row, adapter))
    return result


async def run(
    session: AsyncSession, request: GenerationRequest,
    *, prefer: Optional[str] = None,
) -> GenerationResult:
    """Perform the request with the first provider that manages it.

    Falling through to the next provider matters more than it looks: image
    generation fails for boring reasons — quota, rate limit, a bad minute — and
    charging someone for an outage is the fastest way to lose them.
    """
    request.validate()
    options = await candidates(
        session, request.operation, references=max(1, len(request.references))
    )
    if prefer:
        # Стиль может попросить конкретного исполнителя — например, чтобы
        # сравнить вендоров на одном и том же промпте. Это именно
        # предпочтение, а не жёсткая привязка: если он недоступен, очередь
        # отработает как обычно, и человек получит картинку, а не отказ.
        options.sort(key=lambda pair: pair[0].id != prefer)
    if not options:
        raise GenerationUnavailable(
            f"No provider enabled for {request.operation.value}"
        )

    # Причины падений копятся все, а не только последняя.
    #
    # Раньше в отказе оставалась одна — от последнего кандидата, — и по записи
    # нельзя было понять, что случилось с первыми двумя. Три исполнителя могут
    # упасть по трём разным поводам: у одного кончилась квота, второй ответил
    # четырёхсоткой на параметр, третий не дозвонился. Одна строка вместо трёх
    # превращает разбор в гадание, а разбирать приходится по одной записи в
    # базе: логи живут в консоли и до завтра не доживают.
    failures: list[str] = []
    for row, adapter in options:
        started = time.monotonic()
        try:
            # Модель берётся из строки, а не из настроек адаптера: один
            # адаптер обслуживает несколько поколений вендора, и какое из них
            # работает на этом потоке — данные, а не релиз.
            result = await _attempt(row.id, adapter, request, row.model)
        except Exception as exc:  # noqa: BLE001 — any failure means "next one"
            failures.append(f"{row.id}: {exc!r}")
            logger.warning("Provider %s failed on %s: %r", row.id, request.operation.value, exc)
            continue
        result.duration_ms = int((time.monotonic() - started) * 1000)
        return result

    raise GenerationUnavailable(
        f"All providers failed for {request.operation.value}: " + "; ".join(failures)
    )


async def _attempt(provider_id: str, adapter, request: GenerationRequest, model: Optional[str]):
    """Одна попытка — и вторая, если до вендора не дошло соединение.

    Осечка соединения означает, что запрос не ушёл: повторить его безопасно,
    счёта за него никто не выставит. Так уже устроен текстовый путь, и по той же
    причине — одна плохая секунда сети не должна стоить человеку кадра.

    Таймаут чтения сюда не входит намеренно: там вендор работу получил и может
    её посчитать, а платить дважды за одну картинку — хуже, чем отказать.
    """
    try:
        return await adapter.run(request, model=model)
    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
        logger.warning("Соединение с %s не установилось (%r) — вторая попытка", provider_id, exc)
        return await adapter.run(request, model=model)
