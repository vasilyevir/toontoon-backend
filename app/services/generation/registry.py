"""Which model performs an operation — data, not code.

The adapter lives in Python, the switch lives in the database. Turning a model
on, changing the order, falling back after an outage, running two models side by
side — all of that is a row in ``generation_providers``, not a release (CH-21).
"""
from __future__ import annotations

import logging
import time
from typing import Optional, Sequence

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
from app.services.generation.providers.kling import KlingProvider
from app.services.generation.providers.openai_images import OpenAIImagesProvider
from app.services.generation.providers.pollinations import PollinationsProvider

logger = logging.getLogger("toontoon.generation")

# Every adapter we ship. Presence here does not enable anything: a provider is
# used only if the database says so.
ADAPTERS: dict[str, Provider] = {
    p.id: p
    for p in (
        OpenAIImagesProvider(),
        # Два поколения Kling живут одновременно: третья версия ссылается на
        # снимок из промпта, вторая даёт отдельную ручку на лицо. Какая где
        # работает — решает приоритет в базе.
        KlingProvider("kling"),
        KlingProvider("kling_v2"),
        PollinationsProvider(),
    )
}


async def candidates(
    session: AsyncSession, operation: Operation
) -> list[tuple[m.GenerationProvider, Provider]]:
    """Enabled providers for an operation, best first."""
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
        adapter = ADAPTERS.get(row.id)
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
    options = await candidates(session, request.operation)
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

    last_error: Optional[Exception] = None
    for row, adapter in options:
        started = time.monotonic()
        try:
            # Модель берётся из строки, а не из настроек адаптера: один
            # адаптер обслуживает несколько поколений вендора, и какое из них
            # работает на этом потоке — данные, а не релиз.
            result = await adapter.run(request, model=row.model)
        except Exception as exc:  # noqa: BLE001 — any failure means "next one"
            last_error = exc
            logger.warning("Provider %s failed on %s: %r", row.id, request.operation.value, exc)
            continue
        result.duration_ms = int((time.monotonic() - started) * 1000)
        return result

    raise GenerationUnavailable(
        f"All providers failed for {request.operation.value}: {last_error!r}"
    )
