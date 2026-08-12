"""Chat/session history — server-owned, so every client (web, iOS, ...) gets
the same conversation history without reimplementing local storage.

``GET /api/chats``                 sidebar list (most-recently-active first)
``POST /api/chats``                start a new chat
``GET /api/chats/{id}``            full chat incl. all messages
``PATCH /api/chats/{id}``          rename / set thumbnail
``POST /api/chats/{id}/messages``  append a message (user turn, AI reply,
                                    generation result, error, ...)
``DELETE /api/chats/{id}``         remove a chat
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.security import new_id
from app.deps import Context, optional_context, required_context
from app.models.chat import (
    AddMessageRequest,
    ChatMessage,
    ChatSession,
    ChatSummary,
    CreateChatRequest,
    UpdateChatRequest,
)
from app.services import chat_service

router = APIRouter(prefix="/api/chats", tags=["chats"])


async def _get_owned_chat(chat_id: str, user_id: str) -> ChatSession:
    chat = await chat_service.get(chat_id)
    if not chat or chat.user_id != user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Chat not found")
    return chat


@router.get("", response_model=list[ChatSummary])
async def list_chats(ctx: Optional[Context] = Depends(optional_context)) -> list[ChatSummary]:
    """The sidebar list. Returns ``[]`` when not authenticated (never 401) so
    a client can render an empty state instead of erroring on cold start."""
    if ctx is None:
        return []
    user, _ = ctx
    return await chat_service.list_summaries(user.id)


@router.post("", response_model=ChatSession, status_code=status.HTTP_201_CREATED)
async def create_chat(
    body: CreateChatRequest = CreateChatRequest(), ctx: Context = Depends(required_context)
) -> ChatSession:
    user, _ = ctx
    return await chat_service.create(user.id, label=body.label)


@router.get("/{chat_id}", response_model=ChatSession)
async def get_chat(chat_id: str, ctx: Context = Depends(required_context)) -> ChatSession:
    user, _ = ctx
    return await _get_owned_chat(chat_id, user.id)


@router.patch("/{chat_id}", response_model=ChatSession)
async def update_chat(
    chat_id: str, body: UpdateChatRequest, ctx: Context = Depends(required_context)
) -> ChatSession:
    user, _ = ctx
    chat = await _get_owned_chat(chat_id, user.id)
    return await chat_service.update_meta(chat, label=body.label, image=body.image)


@router.post("/{chat_id}/messages", response_model=ChatMessage, status_code=status.HTTP_201_CREATED)
async def add_message(
    chat_id: str, body: AddMessageRequest, ctx: Context = Depends(required_context)
) -> ChatMessage:
    user, _ = ctx
    chat = await _get_owned_chat(chat_id, user.id)
    message = ChatMessage(id=new_id("msg_"), **body.model_dump())
    await chat_service.add_message(chat, message)
    return message


@router.delete("/{chat_id}")
async def delete_chat(chat_id: str, ctx: Context = Depends(required_context)) -> dict:
    user, _ = ctx
    chat = await _get_owned_chat(chat_id, user.id)
    await chat_service.delete(chat)
    return {"ok": True}
