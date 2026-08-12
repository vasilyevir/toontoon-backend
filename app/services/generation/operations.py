"""What a generation is, independent of who performs it.

Three notions hold this together (CH-21): an **operation** says what to do, a
**style** says what the person will get, a **provider** says what actually made
it. The link is one-way — a style never names a provider, a provider never
knows about styles — and that is what makes adding a model an adapter plus a
row in the registry rather than a change to the pipeline.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Operation(str, Enum):
    TEXT_TO_IMAGE = "text_to_image"
    IMAGE_TO_IMAGE = "image_to_image"
    IMAGE_TO_VIDEO = "image_to_video"
    INPAINT = "inpaint"
    OUTPAINT = "outpaint"
    UPSCALE = "upscale"


# What each operation needs on input. The launch screen in the app is built
# from a style's input_spec, which is derived from this — so a new operation
# ships without a new screen.
REQUIRED_INPUTS: dict[Operation, tuple[str, ...]] = {
    Operation.TEXT_TO_IMAGE: ("prompt",),
    Operation.IMAGE_TO_IMAGE: ("prompt", "image"),
    Operation.IMAGE_TO_VIDEO: ("image",),
    Operation.INPAINT: ("prompt", "image", "mask"),
    Operation.OUTPAINT: ("prompt", "image"),
    Operation.UPSCALE: ("image",),
}


class GenerationUnavailable(RuntimeError):
    """No provider could deliver. The caller refunds and asks to retry."""


@dataclass(slots=True)
class GenerationRequest:
    operation: Operation
    prompt: Optional[str] = None
    negative_prompt: Optional[str] = None
    # Raw bytes, not URLs: a reference photo must not be reachable by a link,
    # and providers that need a URL get a short-lived signed one from the
    # adapter rather than from the whole system.
    image: Optional[bytes] = None
    image_mime: Optional[str] = None
    mask: Optional[bytes] = None
    params: dict = field(default_factory=dict)

    def validate(self) -> None:
        missing = [
            name
            for name in REQUIRED_INPUTS[self.operation]
            if getattr(self, name, None) in (None, "", b"")
        ]
        if missing:
            raise ValueError(
                f"{self.operation.value} requires {', '.join(missing)}"
            )


@dataclass(slots=True)
class GenerationResult:
    data: bytes
    mime: str
    provider_id: str
    model: str
    # Filled by the caller; kept here so the record of what actually happened
    # travels with the result instead of being reconstructed later.
    duration_ms: Optional[int] = None
