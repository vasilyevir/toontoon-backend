"""Provider contract.

A provider declares which operations it can perform and performs them. It does
not know what a style is, what a token costs, or who the user is — those live
above it, which is why swapping a model never reaches into the pipeline.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from app.services.generation.operations import GenerationRequest, GenerationResult, Operation


class Provider(ABC):
    #: Registry id — matches ``generation_providers.id`` in the database.
    id: str = ""

    @property
    @abstractmethod
    def operations(self) -> frozenset[Operation]:
        """What this adapter can actually do today."""

    @property
    @abstractmethod
    def model(self) -> str:
        """Модель по умолчанию — та, что подставится, если строка реестра молчит.

        Пишется в каждую генерацию: модели меняют, и история, где вчерашняя
        работа приписана сегодняшней модели, для сравнения бесполезна.
        """

    def available(self) -> bool:
        """False when the adapter has no credentials — the registry skips it."""
        return True

    @abstractmethod
    async def run(
        self, request: GenerationRequest, *, model: Optional[str] = None
    ) -> GenerationResult:
        """Выполнить запрос указанной моделью — или отказаться.

        Модель приходит из строки реестра, а не из настроек: один адаптер
        обслуживает несколько поколений одного вендора, и какое из них
        работает на этом потоке — вопрос данных, а не релиза (CH-21). Так у
        Kling сосуществуют третья версия на основном потоке и вторая там, где
        нужна явная ручка на лицо.

        Отказ — это способ сказать «бери следующего»: реестр ловит исключение,
        идёт дальше по приоритету и сдаётся, только когда отказались все.
        """
