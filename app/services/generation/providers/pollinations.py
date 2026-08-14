"""Pollinations — free text-to-image, kept as the fallback.

Useful precisely because it costs nothing: it keeps development and smoke tests
running without spending credits, and it is what answers when the paid provider
is down. It cannot edit a photo, so it will never satisfy the photo-first
promise on its own.
"""
from __future__ import annotations

import logging
from typing import Optional
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

logger = logging.getLogger("toontoon.generation.pollinations")


class PollinationsProvider(Provider):
    id = "pollinations"

    @property
    def operations(self) -> frozenset[Operation]:
        return frozenset({Operation.TEXT_TO_IMAGE})

    @property
    def model(self) -> str:
        return settings.pollinations_model

    async def run(
        self, request: GenerationRequest, *, model: Optional[str] = None
    ) -> GenerationResult:
        model = model or self.model
        prompt = quote((request.prompt or "")[:1500], safe="")
        url = (
            f"{settings.pollinations_image_url.rstrip('/')}/prompt/{prompt}"
            f"?model={model}&width=896&height=1600&nologo=true"
        )
        if request.negative_prompt:
            # Раньше здесь стояло 500 символов, а общий негатив занимает 667:
            # хвост обрывался посреди фразы, и вместе с ним терялись запреты на
            # плохой кадр и на текст в картинке — то есть ровно то, что чаще
            # всего портит результат. Целиком он в URL помещается свободно.
            url += f"&negative={quote(request.negative_prompt[:1000], safe='')}"

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
            data=resp.content, mime=mime, provider_id=self.id, model=model
        )
