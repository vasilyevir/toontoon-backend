"""Server-side chat/session history.

Until now, "chats" only existed in the WEB frontend's ``localStorage``
(``arteki_chat_history`` + ``arteki_chat_msgs_{id}``, see
``gen-frontend/src/app/generate/page.tsx``) — the backend only persisted
individual ``Generation`` records, with no concept of a conversation/session
grouping them together. That's invisible to any other client (native app,
reinstalled browser, different device): there is nothing to fetch. This
model + the ``chats`` router make chat history a first-class, server-owned
resource so any client gets it "for free" — mirrors the web's ``Message``/
``HistoryItem`` shapes 1:1 so the web could eventually switch to it too.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class ChatRole(str, Enum):
    USER = "user"
    AI = "ai"


class ChatAttachment(BaseModel):
    id: str
    name: str = ""
    url: str


class ChatMessage(BaseModel):
    id: str
    role: ChatRole
    text: str = ""
    attachments: list[ChatAttachment] = Field(default_factory=list)

    # Generation results rendered inline in the conversation (mirrors the
    # web's Message fields exactly).
    generated_img: Optional[str] = None
    generated_imgs: list[str] = Field(default_factory=list)
    generated_video: Optional[str] = None
    thumbnail_url: Optional[str] = None
    # Back-reference so a client can poll/fetch full generation detail.
    generation_id: Optional[str] = None

    # Postcard text overlay.
    card_text: Optional[str] = None
    card_tile_id: Optional[str] = None

    is_generation_error: bool = False

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ChatSession(BaseModel):
    """Stored under ``chat:{id}``. Indexed per-user in
    ``user:chats:{user_id}`` (most-recently-active first)."""

    id: str
    user_id: str
    label: str = "New chat"
    # Sidebar thumbnail — usually the latest generated image/video thumb.
    image: Optional[str] = None
    messages: list[ChatMessage] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ChatSummary(BaseModel):
    """Lightweight shape for the sidebar list (``GET /api/chats``) — no messages."""

    id: str
    label: str
    image: Optional[str] = None
    created_at: datetime
    updated_at: datetime


# ─── Request bodies ───────────────────────────────────────────────────────────


class CreateChatRequest(BaseModel):
    label: str = Field(default="New chat", max_length=200)


class UpdateChatRequest(BaseModel):
    label: Optional[str] = Field(default=None, max_length=200)
    image: Optional[str] = Field(default=None, max_length=2048)


class AddMessageRequest(BaseModel):
    role: ChatRole
    text: str = Field(default="", max_length=4000)
    attachments: list[ChatAttachment] = Field(default_factory=list)
    generated_img: Optional[str] = None
    generated_imgs: list[str] = Field(default_factory=list)
    generated_video: Optional[str] = None
    thumbnail_url: Optional[str] = None
    generation_id: Optional[str] = None
    card_text: Optional[str] = None
    card_tile_id: Optional[str] = None
    is_generation_error: bool = False
