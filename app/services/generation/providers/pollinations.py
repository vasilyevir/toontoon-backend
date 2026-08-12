"""Pollinations — free text-to-image, kept as the fallback.

Useful precisely because it costs nothing: it keeps development and smoke tests
running without spending credits, and it is what answers when the paid provider
is down. It cannot edit a photo, so it will never satisfy the photo-first
promise on its own.
"""
from __future__ import annotations

import logging
from urllib.parse import quote

import httpx

from app.config import settings
from app.services.generation.operations import (
    GenerationRequest,
    GenerationResult,
    GenerationUnavailable,
    Operation,
)
from app.services.generation.providers.base import Provider

logger = logging.getLogger("arteki.generation.pollinations")


class PollinationsProvider(Provider):
    id = "pollinations"

    @property
    def operations(self) -> frozenset[Operation]:
        return frozenset({Operation.TEXT_TO_IMAGE})

    @property
    def model(self) -> str:
        return settings.pollinations_model

    async def run(self, request: GenerationRequest) -> GenerationResult:
        prompt = quote((request.prompt or "")[:1500], safe="")
        url = (
            f"{settings.pollinations_image_url.rstrip('/')}/prompt/{prompt}"
            f"?model={settings.pollinations_model}&width=1024&height=1024&nologo=true"
        )
        if request.negative_prompt:
            url += f"&negative={quote(request.negative_prompt[:500], safe='')}"

        headers = {}
        if settings.pollinations_token:
            headers["Authorization"] = f"Bearer {settings.pollinations_token}"

        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)

        mime = resp.headers.get("content-type", "").split(";")[0].strip()
        if resp.status_code != 200 or not mime.startswith("image/") or not resp.content:
            raise GenerationUnavailable(
                f"Pollinations HTTP {resp.status_code}, content-type={mime!r}"
            )
        return GenerationResult(
            data=resp.content, mime=mime, provider_id=self.id, model=self.model
        )
