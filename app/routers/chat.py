"""Chat — one continuous thread per user (CH-20).

Replaces the thread list (`/api/chats/*`): there is a single conversation, its
history is kept, and "Clear" starts a new context without deleting anything.
Finding an old result is the history tab's job, not the chat's.

Routes:
  POST /api/chat          — send a message, get the reply, both are stored
  GET  /api/chat/messages — a page of the thread, newest first
  POST /api/chat/clear    — start a new context, keep the transcript
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import models as m
from app.db.repositories import chat as chat_repo
from app.db.session import get_session as get_db_session
from app.deps import Context, optional_context, required_context
from app.services import gpt as gpt_service

router = APIRouter(prefix="/api", tags=["chat"])


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    # Kept for compatibility with the current app build, but ignored: the
    # server owns the context now and takes it from the stored thread, so a
    # client can no longer decide what the model remembers.
    history: list[ChatMessage] = Field(default_factory=list, max_length=20)


class ChatResponse(BaseModel):
    reply: str


class StoredMessage(BaseModel):
    id: int
    role: str
    content: Optional[str] = None
    generation_id: Optional[str] = None
    result_url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    created_at: datetime


def _serialize(row: m.ChatMessage, result_media_id: Optional[str]) -> StoredMessage:
    return StoredMessage(
        id=row.id,
        role=row.role,
        content=row.content,
        generation_id=row.generation_id,
        result_url=f"/api/media/{result_media_id}" if result_media_id else None,
        thumbnail_url=f"/api/media/{result_media_id}?thumb=true" if result_media_id else None,
        created_at=row.created_at,
    )


@router.post("/chat", response_model=ChatResponse)
async def chat(
    body: ChatRequest,
    ctx: Optional[Context] = Depends(optional_context),
    db: AsyncSession = Depends(get_db_session),
) -> ChatResponse:
    """Answer, and store both sides of the turn.

    Works without a session too — a guest gets a session on first launch, so in
    practice this only happens for a probe or a browser without one; nothing is
    stored then.
    """
    if ctx is None:
        reply = await gpt_service.chat_reply(message=body.message, history=[])
        return ChatResponse(reply=reply)

    user, _ = ctx
    history = await chat_repo.context_messages(db, user, limit=settings.chat_context_messages)
    reply = await gpt_service.chat_reply(message=body.message, history=history)

    await chat_repo.add_message(db, user_id=user.id, role="user", content=body.message)
    await chat_repo.add_message(db, user_id=user.id, role="assistant", content=reply)
    return ChatResponse(reply=reply)


@router.get("/chat/messages", response_model=list[StoredMessage])
async def messages(
    ctx: Context = Depends(required_context),
    db: AsyncSession = Depends(get_db_session),
    limit: int = Query(default=30, ge=1, le=100),
    before: Optional[datetime] = Query(default=None, description="Курсор: created_at предыдущей страницы"),
) -> list[StoredMessage]:
    user, _ = ctx
    rows = await chat_repo.list_messages(db, user.id, limit=limit, before=before)

    # Results are rendered inline in the thread, so a message that produced one
    # carries its media link.
    media_by_generation: dict[str, Optional[str]] = {}
    generation_ids = [r.generation_id for r in rows if r.generation_id]
    if generation_ids:
        from sqlalchemy import select

        stmt = select(m.Generation.id, m.Generation.result_media_id).where(
            m.Generation.id.in_(generation_ids)
        )
        media_by_generation = {gid: mid for gid, mid in (await db.execute(stmt)).all()}

    return [
        _serialize(row, media_by_generation.get(row.generation_id) if row.generation_id else None)
        for row in rows
    ]


@router.post("/chat/clear")
async def clear(
    ctx: Context = Depends(required_context),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Start a new conversation.

    The transcript stays and can be scrolled back to — only the model's memory
    is reset. The client should draw a visible divider at this point, otherwise
    scrolling up and finding a conversation the assistant "forgot" looks broken.
    """
    user, _ = ctx
    await chat_repo.clear_context(db, user.id)
    return {"ok": True}
