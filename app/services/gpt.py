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
from app.services import prompt_style

_OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"

# ─── System prompts ──────────────────────────────────────────────────────────

# The "smart builder" (ТЗ §2–3): turns a non-technical user's quick answers into
# ONE clean English SCENE. It writes ONLY the scene — the style anchor and the
# technical block are added deterministically by prompt_style.assemble().
_SCENE_SYSTEM = """You translate a non-technical user's quick answers into ONE vivid
English SCENE description for an image generator. The audience is people aged 40–60
who write casually, with filler words, and expect you to "figure it out".

Write ONLY the scene (30–60 words): the subject, what they are doing, the setting,
and the mood/emotion. Extract concrete visual meaning and drop conversational filler.

Strict rules:
- Output ONLY the scene. NO style words, NO camera/technical words, NO quotes, NO
  explanations — the system adds the visual style and technical quality separately.
- Apply sensible defaults: if a child is implied, use a cute child character; infer
  gender from the name when given; greetings → warm cheerful mood; morning wishes →
  calm peaceful mood; pick a fitting setting for the occasion.
- Characters must have a genuine, warm, friendly expression. Never scary or eerie faces.
- NEVER use these words: beautiful, high quality, realistic, perfect, good, nice, 4k, hd, masterpiece.
- If a greeting/announcement text is provided, DO NOT spell out or draw any letters.
  Instead describe a clean empty area where text can be placed later.
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
    """Return a full image prompt built per the spec methodology.

    GPT writes the cleaned SCENE; ``prompt_style.assemble`` wraps it with the
    chosen style anchor (first) and the technical block (last). Returns "" if
    OpenAI is not configured or the call fails — the caller uses the mechanical builder.
    """
    if not settings.openai_enabled:
        return ""

    style_key = prompt_style.map_style(style)
    is_text = prompt_style.is_text_tile(tile.category.value) if tile else False

    # Compose the user message for the scene writer.
    parts: list[str] = []
    if tile:
        parts.append(f"Card type: {tile.title} ({tile.category.value})")
        for q in tile.questions:
            val = answers.get(q.id)
            if not val:
                continue
            if q.id == "text":
                parts.append(f"Greeting to leave clean space for (do NOT draw the letters): {val}")
            else:
                parts.append(f"{q.text} {val}")
    if free_text:
        parts.append(f"User idea: {free_text}")
    if photo_description:
        parts.append(f"Reference photo shows: {photo_description}")

    user_msg = "\n".join(parts) or "A warm, friendly greeting image"

    messages = [
        {"role": "system", "content": _SCENE_SYSTEM},
        {"role": "user", "content": user_msg},
    ]
    try:
        scene = await _call(messages, max_tokens=160)
    except Exception:
        return ""

    if not scene:
        return ""
    return prompt_style.assemble(scene, style_key=style_key, is_text=is_text)


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
