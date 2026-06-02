"""GPT-4o mini — two roles in the ARTEKI product.

Role 1 — Prompt builder
  Takes a tile + user answers and returns a rich English prompt
  ready for the image generation model. When OpenAI is not configured
  we fall back to the simple mechanical builder.

Role 2 — Chat assistant
  Handles free-form conversation on the frontend: greets the user,
  suggests tiles, asks follow-up questions, stays on-brand as Arteki.
  POST /api/chat uses this.
"""
from __future__ import annotations

from typing import Optional

import httpx

from app.config import settings
from app.models.tile import Tile

_OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"

# ─── System prompts ──────────────────────────────────────────────────────────

_PROMPT_BUILDER_SYSTEM = """You are an expert image-generation prompt writer for ARTEKI,
an AI card and content creator for everyday people (mostly 40–60 year olds).

Your task: given a tile name and the user's answers, write ONE short, vivid
English prompt (max 60 words) for an image generation model (FLUX / DALL-E).

Rules:
- Write ONLY the prompt, no explanations, no quotes.
- Use concrete visual details: lighting, colors, composition, style.
- Make it warm, beautiful and matching the occasion.
- Always end with the quality suffix:
  "high quality, highly detailed, professional, sharp focus, beautiful lighting"
"""

_CHAT_SYSTEM = """You are Arteki — a friendly, warm AI assistant that helps people
create beautiful images, postcards, announcements and videos.

Your users are mostly 40–60 years old and not very tech-savvy. Be simple,
kind and encouraging. Speak in short sentences.

Your capabilities:
- Generate images (1 TEKI): Good morning, Cute animal, Beautiful nature,
  Cartoon character, Fairytale landscape, Have a nice day
- Generate postcards (1 TEKI): Birthday, Jubilee, Wedding, Anniversary,
  New Year, March 8, Victory Day, Easter, Mother's Day, Just because
- Generate announcements (1 TEKI): Cafe, Beauty salon, Handyman, Tutor,
  Cakes & baking, Property rental, Selling items
- Generate videos (2 TEKI): Animate photo, Animate pet, Cartoon character,
  Video greeting, Living nature, Cute animal, Good morning, Inspiring video

When the user describes what they want, suggest the best matching tile.
Ask 1–2 short clarifying questions if needed, then confirm and offer to generate.
Never ask more than 2 questions in a row.
Keep replies under 3 sentences.
"""


# ─── Helpers ─────────────────────────────────────────────────────────────────


async def _call(messages: list[dict], *, max_tokens: int = 300) -> str:
    """Make a chat completion call and return the assistant's text."""
    headers = {
        "Authorization": f"Bearer {settings.openai_api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": settings.openai_model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.7,
    }
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(_OPENAI_CHAT_URL, headers=headers, json=payload)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()


# ─── Role 1: Prompt builder ──────────────────────────────────────────────────


async def build_prompt(
    *,
    tile: Optional[Tile],
    answers: dict[str, str],
    free_text: Optional[str],
    style: Optional[str],
    photo_description: Optional[str] = None,
) -> str:
    """Return a rich image-generation prompt via GPT-4o mini.

    Falls back to None if OpenAI is not configured — caller uses the basic builder.
    """
    if not settings.openai_enabled:
        return ""

    # Compose the user message.
    parts: list[str] = []
    if tile:
        parts.append(f"Tile: {tile.title} ({tile.category.value})")
        for q in tile.questions:
            val = answers.get(q.id)
            if val:
                parts.append(f"{q.text} → {val}")
    if free_text:
        parts.append(f"User description: {free_text}")
    if style:
        parts.append(f"Style: {style}")
    if photo_description:
        parts.append(f"Reference photo: {photo_description}")

    user_msg = "\n".join(parts) or "Generic beautiful image"

    messages = [
        {"role": "system", "content": _PROMPT_BUILDER_SYSTEM},
        {"role": "user", "content": user_msg},
    ]
    try:
        return await _call(messages, max_tokens=120)
    except Exception:
        return ""


# ─── Role 2: Chat assistant ──────────────────────────────────────────────────


async def chat_reply(
    *,
    message: str,
    history: list[dict],
) -> str:
    """Return Arteki's reply to a user message in the chat.

    ``history`` is a list of ``{"role": "user"|"assistant", "content": str}``.
    Falls back to a static reply if OpenAI is not configured.
    """
    if not settings.openai_enabled:
        return (
            "Hi! I'm Arteki. Tell me what you'd like to create today — "
            "a postcard, image, announcement or video?"
        )

    messages = [{"role": "system", "content": _CHAT_SYSTEM}]
    messages.extend(history[-10:])  # Keep last 10 turns for context.
    messages.append({"role": "user", "content": message})

    try:
        return await _call(messages, max_tokens=200)
    except Exception:
        return "Sorry, I'm having trouble right now. Please try again in a moment."
