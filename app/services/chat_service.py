"""Chat session persistence in Redis.

Key layout
----------
* ``chat:{id}``            -> ChatSession JSON (messages embedded, capped)
* ``user:chats:{user_id}`` -> Redis list of chat ids, most-recently-active first
"""
from __future__ import annotations

from typing import Optional

from app.core.security import new_id
from app.models.chat import ChatMessage, ChatSession, ChatSummary
from app.redis_client import get_client

_MAX_CHATS_PER_USER = 200
_MAX_MESSAGES_PER_CHAT = 200


def _chat_key(chat_id: str) -> str:
    return f"chat:{chat_id}"


def _user_list_key(user_id: str) -> str:
    return f"user:chats:{user_id}"


async def save(chat: ChatSession) -> None:
    redis = get_client()
    await redis.set(_chat_key(chat.id), chat.model_dump_json())


async def get(chat_id: str) -> Optional[ChatSession]:
    redis = get_client()
    raw = await redis.get(_chat_key(chat_id))
    return ChatSession.model_validate_json(raw) if raw else None


async def _touch(user_id: str, chat_id: str) -> None:
    """Move a chat to the front of the user's MRU list (sidebar order)."""
    redis = get_client()
    list_key = _user_list_key(user_id)
    await redis.lrem(list_key, 0, chat_id)
    await redis.lpush(list_key, chat_id)
    await redis.ltrim(list_key, 0, _MAX_CHATS_PER_USER - 1)


async def create(user_id: str, label: str = "New chat") -> ChatSession:
    chat = ChatSession(id=new_id("chat_"), user_id=user_id, label=label)
    await save(chat)
    await _touch(user_id, chat.id)
    return chat


async def list_summaries(user_id: str) -> list[ChatSummary]:
    redis = get_client()
    ids = await redis.lrange(_user_list_key(user_id), 0, _MAX_CHATS_PER_USER - 1)
    if not ids:
        return []
    raws = await redis.mget(*[_chat_key(cid) for cid in ids])
    out: list[ChatSummary] = []
    for raw in raws:
        if not raw:
            continue
        chat = ChatSession.model_validate_json(raw)
        out.append(
            ChatSummary(
                id=chat.id, label=chat.label, image=chat.image,
                created_at=chat.created_at, updated_at=chat.updated_at,
            )
        )
    return out


async def update_meta(chat: ChatSession, *, label: Optional[str] = None, image: Optional[str] = None) -> ChatSession:
    from datetime import datetime, timezone

    if label is not None:
        chat.label = label
    if image is not None:
        chat.image = image
    chat.updated_at = datetime.now(timezone.utc)
    await save(chat)
    await _touch(chat.user_id, chat.id)
    return chat


async def add_message(chat: ChatSession, message: ChatMessage) -> ChatSession:
    from datetime import datetime, timezone

    chat.messages.append(message)
    chat.messages = chat.messages[-_MAX_MESSAGES_PER_CHAT:]

    # Auto-derive the sidebar thumbnail from the latest generation result
    # (mirrors the web's HistoryItem.image behavior) unless already set by
    # the caller via update_meta.
    if message.generated_video:
        chat.image = message.thumbnail_url or message.generated_video
    elif message.generated_imgs:
        chat.image = message.generated_imgs[-1]
    elif message.generated_img:
        chat.image = message.generated_img

    chat.updated_at = datetime.now(timezone.utc)
    await save(chat)
    await _touch(chat.user_id, chat.id)
    return chat


async def delete(chat: ChatSession) -> None:
    redis = get_client()
    await redis.delete(_chat_key(chat.id))
    await redis.lrem(_user_list_key(chat.user_id), 0, chat.id)
