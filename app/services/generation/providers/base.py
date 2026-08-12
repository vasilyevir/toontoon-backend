"""Provider contract.

A provider declares which operations it can perform and performs them. It does
not know what a style is, what a token costs, or who the user is — those live
above it, which is why swapping a model never reaches into the pipeline.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

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
        """The concrete model, recorded on every generation it produces.

        Stored per result rather than read from config at display time: models
        get swapped, and a history that claims yesterday's work was made by
        today's model is useless for comparing them.
        """

    def available(self) -> bool:
        """False when the adapter has no credentials — the registry skips it."""
        return True

    @abstractmethod
    async def run(self, request: GenerationRequest) -> GenerationResult:
        """Perform the generation or raise.

        Raising is how a provider says "try the next one": the registry catches
        it, moves on, and only gives up when everyone has failed.
        """
