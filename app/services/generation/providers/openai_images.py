"""OpenAI Images.

Two operations from one adapter: generation from a prompt, and — the part the
product actually needs (CH-19) — editing an uploaded photo, which is the only
way "turn your photo into…" can mean the person on the photo.

``image_to_image`` is written but **disabled in the registry** until it has been
tried on real faces: the question is not whether the call works, it is whether
the result is recognisably the same person, and that is answered by looking, not
by a status code.
"""
from __future__ import annotations

import base64
import logging
from typing import Optional

import httpx

from app.config import settings
from app.services.generation.operations import (
    GenerationRequest,
    GenerationResult,
    GenerationUnavailable,
    Operation,
)
from app.services.generation.providers.base import Provider

logger = logging.getLogger("arteki.generation.openai")

_API = "https://api.openai.com/v1"


class OpenAIImagesProvider(Provider):
    id = "openai_images"

    @property
    def operations(self) -> frozenset[Operation]:
        return frozenset({Operation.TEXT_TO_IMAGE, Operation.IMAGE_TO_IMAGE})

    @property
    def model(self) -> str:
        return settings.openai_image_model

    def available(self) -> bool:
        return settings.openai_enabled

    async def run(self, request: GenerationRequest) -> GenerationResult:
        if request.operation is Operation.IMAGE_TO_IMAGE:
            data = await self._edit(request)
        else:
            data = await self._generate(request)
        return GenerationResult(
            data=data, mime="image/png", provider_id=self.id, model=self.model
        )

    async def _generate(self, request: GenerationRequest) -> bytes:
        body = {
            "model": self.model,
            "prompt": (request.prompt or "")[:32000],
            "n": 1,
            "size": settings.openai_image_size,
        }
        if settings.openai_image_quality in {"low", "medium", "high", "auto"}:
            body["quality"] = settings.openai_image_quality

        async with httpx.AsyncClient(timeout=settings.openai_image_timeout) as client:
            resp = await client.post(
                f"{_API}/images/generations", json=body, headers=self._headers()
            )
        return self._decode(resp)

    async def _edit(self, request: GenerationRequest) -> bytes:
        """Edit the uploaded image. This is the call the whole photo-first
        product depends on."""
        files = {
            "image": ("photo.png", request.image, request.image_mime or "image/png"),
        }
        if request.mask:
            files["mask"] = ("mask.png", request.mask, "image/png")
        data = {
            "model": self.model,
            "prompt": (request.prompt or "")[:32000],
            "n": "1",
            "size": settings.openai_image_size,
        }
        async with httpx.AsyncClient(timeout=settings.openai_image_timeout) as client:
            resp = await client.post(
                f"{_API}/images/edits", data=data, files=files, headers=self._headers()
            )
        return self._decode(resp)

    @staticmethod
    def _headers() -> dict:
        return {"Authorization": f"Bearer {settings.openai_api_key}"}

    @staticmethod
    def _decode(resp: httpx.Response) -> bytes:
        if resp.status_code != 200:
            raise GenerationUnavailable(
                f"OpenAI HTTP {resp.status_code}: {resp.text[:300]}"
            )
        payload = resp.json()["data"][0]
        if "b64_json" in payload:
            return base64.b64decode(payload["b64_json"])
        raise GenerationUnavailable("OpenAI returned no image data")
