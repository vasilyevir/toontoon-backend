from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class GenerationType(str, Enum):
    IMAGE = "image"
    VIDEO = "video"


class GenerationStatus(str, Enum):
    QUEUED = "queued"
    DONE = "done"
    FAILED = "failed"


class Generation(BaseModel):
    """A single generation record. Stored under ``generation:{id}`` and indexed
    per-user in the list ``user:generations:{user_id}``."""

    id: str
    user_id: str
    type: GenerationType = GenerationType.IMAGE
    status: GenerationStatus = GenerationStatus.QUEUED

    tile_id: Optional[str] = None
    tile_label: Optional[str] = None
    prompt: str = ""
    result_url: Optional[str] = None
    # For videos: a JPEG thumbnail extracted from the first frame (shown in gallery/sidebar).
    thumbnail_url: Optional[str] = None

    # Wallet bookkeeping.
    payment_id: Optional[str] = None
    cost: int = 0

    # Public sharing.
    share_id: Optional[str] = None

    # If the request came from within a chat session, the result (or error,
    # on failure) is appended there automatically — crucial for video, whose
    # completion happens in a background job long after the HTTP response.
    chat_id: Optional[str] = None

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ─── Request / response bodies ──────────────────────────────────────────────


class GenerateRequest(BaseModel):
    """Body for ``POST /api/generate``.

    A request is either tile-driven (``tile_id`` + ``answers``) or free-form
    (``prompt``). ``style`` is the free-form follow-up answer.
    """

    type: GenerationType = GenerationType.IMAGE
    tile_id: Optional[str] = None
    answers: dict[str, str] = Field(default_factory=dict)
    prompt: Optional[str] = None
    style: Optional[str] = None
    # Стиль из каталога: «нажал на пример — подставил своё фото». Промпт тогда
    # берётся из строки стиля, а не сочиняется: витрина показывает конкретный
    # результат, и получить его можно только тем же текстом, которым он сделан.
    style_id: Optional[str] = None
    photo_url: Optional[str] = None
    # Optional: id of a chat session (POST /api/chats) to append the result
    # (or error) to automatically. Omit to generate without chat history.
    chat_id: Optional[str] = None


class GenerateResponse(BaseModel):
    id: str
    url: str
    type: GenerationType
    balance: int
    prompt: str
    # "done" for images (synchronous) or "queued" for videos (async + polled).
    status: GenerationStatus = GenerationStatus.DONE


class CreateGenerationRequest(BaseModel):
    """Body for ``POST /api/generations`` — reserve a queued record up-front."""

    type: GenerationType = GenerationType.IMAGE
    tile_id: Optional[str] = None
    prompt: str = ""


class ShareResponse(BaseModel):
    share_id: str
    share_url: str
