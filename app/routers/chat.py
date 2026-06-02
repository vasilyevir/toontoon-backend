"""Chat endpoint — GPT-4o mini as the Arteki conversational assistant.

POST /api/chat
  Body:  { message: str, history: [{ role, content }] }
  Reply: { reply: str }

The frontend sends the last N messages as history so GPT has context.
No auth required — anyone can chat; auth is only needed to generate.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.services import gpt as gpt_service

router = APIRouter(prefix="/api", tags=["chat"])


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    history: list[ChatMessage] = Field(default_factory=list, max_length=20)


class ChatResponse(BaseModel):
    reply: str


@router.post("/chat", response_model=ChatResponse)
async def chat(body: ChatRequest) -> ChatResponse:
    history = [{"role": m.role, "content": m.content} for m in body.history]
    reply = await gpt_service.chat_reply(message=body.message, history=history)
    return ChatResponse(reply=reply)
