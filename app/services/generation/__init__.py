"""Generation core (CH-21).

Operation → provider, with the styles catalogue on one side and model adapters
on the other. Adding a model is an adapter plus a registry row; adding an
operation is an entry in ``operations.py`` and the input spec that goes with it.
"""
from app.services.generation.operations import (
    GenerationRequest,
    GenerationResult,
    GenerationUnavailable,
    Operation,
)
from app.services.generation.registry import run

__all__ = [
    "GenerationRequest",
    "GenerationResult",
    "GenerationUnavailable",
    "Operation",
    "run",
]
