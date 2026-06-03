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

import logging
from typing import Optional

import httpx

log = logging.getLogger(__name__)

# Keywords that indicate a living subject (people, animals, characters).
# Used for auto-selecting the style anchor when no explicit style is given.
_LIVING_KEYWORDS = {
    # people
    "person", "people", "man", "woman", "girl", "boy", "child", "baby", "lady",
    "gentleman", "character", "hero", "princess", "wizard", "knight", "fairy",
    "elf", "santa", "grandma", "grandpa", "grandmother", "grandfather",
    # animals
    "animal", "pet", "dog", "cat", "kitten", "puppy", "rabbit", "bunny", "bear",
    "panda", "lion", "tiger", "fox", "wolf", "deer", "horse", "elephant",
    "monkey", "gorilla", "bird", "owl", "parrot", "penguin", "duck", "chick",
    "fish", "shark", "dolphin", "whale", "frog", "turtle", "snake",
    "hamster", "rat", "mouse", "pig", "cow", "sheep", "goat", "chicken",
    # fantasy creatures
    "dragon", "unicorn", "dinosaur", "monster", "creature", "beast",
    # russian common terms
    "человек", "персонаж", "животное", "собака", "кошка", "птица", "рыба",
    "кролик", "медведь", "лиса", "волк", "лошадь", "дракон", "кот", "пёс",
}


def _has_living_subject(text: str) -> bool:
    """Return True if the text appears to describe a living subject (person or animal)."""
    words = {w.strip(".,!?;:\"'()") for w in text.lower().split()}
    return bool(words & _LIVING_KEYWORDS)

from app.config import settings
from app.models.tile import Tile
from app.services import card_prompts, picture_prompts, prompt_style

_TEMPLATING_SYSTEM = (
    "You are a precise prompt-templating engine for an image generator. "
    "Follow the user's instruction exactly, keep all fixed blocks verbatim, and "
    "output a single line in the form: final prompt | negative prompt. "
    "No preamble, no quotes, no extra lines."
)

_OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"

# ─── System prompts ──────────────────────────────────────────────────────────

# The "smart builder" (ТЗ §2–3): turns a non-technical user's quick answers into
# ONE clean English SCENE. It writes ONLY the scene — the style anchor and the
# technical block are added deterministically by prompt_style.assemble().
_SCENE_SYSTEM = """You translate a non-technical user's quick answers into ONE vivid
English SCENE description for an image generator. The audience is people aged 40–60
who write casually, with filler words, and expect you to "figure it out".

Write ONLY the scene (30–60 words): the subject, what they are doing or how it looks,
the setting, and the mood. Extract concrete visual meaning and drop conversational filler.

Strict rules:
- Output ONLY the scene. NO style words, NO camera/technical words, NO quotes, NO
  explanations — the system adds the visual style and technical quality separately.
- CRITICAL: Describe ONLY what the user explicitly asked for.
  * If the user asks for a vehicle, building, object, landscape, food, or product —
    draw ONLY that subject. Do NOT add any people, characters, or animals.
  * Only include people/animals if the user explicitly mentions them.
- Infer gender from a name when given. Greetings → warm cheerful mood. Morning wishes →
  calm peaceful mood. Pick a fitting setting for the occasion.
- If characters ARE present, give them a genuine, warm, friendly expression. Never scary.
- NEVER use these words: beautiful, high quality, realistic, perfect, good, nice, 4k, hd, masterpiece.
- If a greeting/announcement text is provided, DO NOT spell out or draw any letters.
  Instead describe a clean empty area where text can be placed later.
"""

_CHAT_SYSTEM = """You are Arteki — a friendly, warm AI assistant that helps people
create beautiful images, postcards and videos.

Your users are mostly 40–60 years old and not very tech-savvy. Be simple,
kind and encouraging. Speak in short sentences.

Your capabilities:
- Generate images (1 TEKI): Cartoon character, Cute animal, Birds, Fish,
  Beautiful nature, Food
- Generate postcards (1 TEKI): Birthday, Milestone Birthday, Valentine's Day,
  Wedding, Anniversary, Mother's Day, Father's Day, Easter, Thanksgiving,
  New Year / Christmas, Graduation, Get Well Soon, Just Because,
  Good morning, Have a nice day
- Generate videos (2 TEKI): Animate photo, Animate pet, Cartoon character,
  Video greeting, Living nature, Cute animal, Good morning, Inspiring video

When the user describes what they want, suggest the best matching tile.
Ask 1–2 short clarifying questions if needed, then confirm and offer to generate.
Never ask more than 2 questions in a row.
Keep replies under 3 sentences.
"""


# ─── Helpers ─────────────────────────────────────────────────────────────────


async def _call(messages: list[dict], *, max_tokens: int = 300, temperature: float = 0.7) -> str:
    """Make a chat completion call and return the assistant's text."""
    headers = {
        "Authorization": f"Bearer {settings.openai_api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": settings.openai_model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(_OPENAI_CHAT_URL, headers=headers, json=payload)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()


def _split_prompt_negative(text: str) -> tuple[str, str]:
    """Parse a ``final prompt | negative prompt`` line from the LLM."""
    text = text.strip().strip("`").strip()
    if "|" in text:
        left, right = text.split("|", 1)
        prompt, negative = left.strip(), right.strip()
    else:
        prompt, negative = text, prompt_style.NEGATIVE_PROMPT

    for label in ("final prompt:", "prompt:"):
        if prompt.lower().startswith(label):
            prompt = prompt[len(label):].strip()
    for label in ("negative prompt:", "negative:"):
        if negative.lower().startswith(label):
            negative = negative[len(label):].strip()

    prompt = " ".join(prompt.split())  # collapse newlines/whitespace
    negative = " ".join(negative.split()) or prompt_style.NEGATIVE_PROMPT
    return prompt, negative


# ─── Role 1: Prompt builder ──────────────────────────────────────────────────


async def build_prompt(
    *,
    tile: Optional[Tile],
    answers: dict[str, str],
    free_text: Optional[str],
    style: Optional[str],
    photo_description: Optional[str] = None,
) -> tuple[str, str]:
    """Return ``(prompt, negative_prompt)`` for the image generator.

    * For the 6 picture tiles → fill the exact per-tile template
      (prompt-templates-pictures spec), STRUCTURED or FREE_TEXT mode.
    * For everything else → GPT writes a SCENE wrapped by the style anchor +
      technical block, paired with the shared negative prompt.

    Returns ``("", "")`` if OpenAI is not configured or the call fails — the
    caller then uses the mechanical builder.
    """
    if not settings.openai_enabled:
        return "", ""

    # ── Picture / card tiles: fill the exact per-tile template ───────────────
    if tile is not None and (picture_prompts.is_picture_tile(tile.id) or card_prompts.is_card_tile(tile.id)):
        has_answers = any(answers.get(q.id) for q in tile.questions)
        if picture_prompts.is_picture_tile(tile.id):
            tpl = picture_prompts.TEMPLATES[tile.id]
            if not has_answers and free_text:
                instruction = picture_prompts.build_free_text_instruction(tpl, free_text)
            else:
                # "question: value" lines give GPT context for placeholder mapping.
                named = {q.text.rstrip("?"): answers[q.id] for q in tile.questions if answers.get(q.id)}
                instruction = picture_prompts.build_structured_instruction(tpl, named)
                if free_text:
                    instruction += f"\n\nEXTRA USER NOTE (fold into the scene): {free_text}"
        else:
            tpl = card_prompts.TEMPLATES[tile.id]
            if not has_answers and free_text:
                instruction = card_prompts.build_free_text_instruction(tpl, free_text)
            else:
                # Card question ids already match the template field keys.
                named = {q.id: answers[q.id] for q in tile.questions if answers.get(q.id)}
                instruction = card_prompts.build_structured_instruction(tpl, named)
                if free_text:
                    instruction += f"\n\nEXTRA USER NOTE (fold into the scene mood): {free_text}"
        if photo_description:
            instruction += f"\n\nREFERENCE PHOTO SHOWS: {photo_description}"

        messages = [
            {"role": "system", "content": _TEMPLATING_SYSTEM},
            {"role": "user", "content": instruction},
        ]
        try:
            raw = await _call(messages, max_tokens=500, temperature=0.4)
        except Exception:
            return "", ""
        if not raw:
            return "", ""
        return _split_prompt_negative(raw)

    # ── Everything else: scene + style anchor + technical block ──────────────
    if style:
        style_key = prompt_style.map_style(style)
    else:
        # Auto-select anchor: living subjects → 3d_cartoon; objects/scenes → scene_cozy.
        # This prevents the character-design anchor from biasing inanimate generations
        # (e.g. "Porsche car" would otherwise get "cheerful charming character design").
        combined_text = " ".join(filter(None, [free_text, tile.title if tile else ""]))
        style_key = "3d_cartoon" if _has_living_subject(combined_text) else "scene_cozy"
    is_text = prompt_style.is_text_tile(tile.category.value) if tile else False

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
        return "", ""

    if not scene:
        return "", ""
    prompt = prompt_style.assemble(scene, style_key=style_key, is_text=is_text)
    log.info("[build_prompt] style=%s → key=%s | scene: %s", style, style_key, scene[:120])
    log.info("[build_prompt] final_prompt: %s", prompt[:200])
    return prompt, prompt_style.NEGATIVE_PROMPT


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
            "a postcard, image or video?"
        )

    messages = [{"role": "system", "content": _CHAT_SYSTEM}]
    messages.extend(history[-10:])  # Keep last 10 turns for context.
    messages.append({"role": "user", "content": message})

    try:
        return await _call(messages, max_tokens=200)
    except Exception:
        return "Sorry, I'm having trouble right now. Please try again in a moment."
