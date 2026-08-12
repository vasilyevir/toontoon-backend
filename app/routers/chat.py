"""Chat endpoint — GPT-4o mini as the Arteki conversational assistant.

POST /api/chat
  Body:  { message: str, history: [{ role, content }] }
  Reply: { reply: str }

The frontend sends the last N messages as history so GPT has context.
No auth required — anyone can chat; auth is only needed to generate.
"""
from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.core.security import new_id
from app.deps import Context, optional_context
from app.services import chat_service, gpt as gpt_service

router = APIRouter(prefix="/api", tags=["chat"])


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    history: list[ChatMessage] = Field(default_factory=list, max_length=20)
    # Optional: id of a chat session (POST /api/chats) to persist this turn
    # to. Silently ignored if missing/not owned — /api/chat itself stays
    # usable with no auth for the assistant-suggests-a-tile flow.
    chat_id: Optional[str] = None


class ChatResponse(BaseModel):
    reply: str


@router.post("/chat", response_model=ChatResponse)
async def chat(body: ChatRequest, ctx: Optional[Context] = Depends(optional_context)) -> ChatResponse:
    history = [{"role": m.role, "content": m.content} for m in body.history]
    reply = await gpt_service.chat_reply(message=body.message, history=history)

    if body.chat_id and ctx is not None:
        from app.models.chat import ChatMessage as StoredMessage
        from app.models.chat import ChatRole

        user, _ = ctx
        session = await chat_service.get(body.chat_id)
        if session is not None and session.user_id == user.id:
            await chat_service.add_message(
                session, StoredMessage(id=new_id("msg_"), role=ChatRole.USER, text=body.message)
            )
            await chat_service.add_message(
                session, StoredMessage(id=new_id("msg_"), role=ChatRole.AI, text=reply)
            )

    return ChatResponse(reply=reply)
