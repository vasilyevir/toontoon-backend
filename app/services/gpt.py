"""GPT-4o mini — two roles in the TOONTOON product.

Role 1 — Prompt builder
  Takes a tile + user answers and returns a rich English prompt
  ready for the image generation model. When OpenAI is not configured
  we fall back to the simple mechanical builder.

Role 2 — Chat assistant
  Handles free-form conversation on the frontend: greets the user,
  suggests tiles, asks follow-up questions, stays on-brand as Toontoon.
  POST /api/chat uses this.
"""
from __future__ import annotations

import asyncio
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
    # russian — people
    "человек", "люди", "мужчина", "женщина", "девочка", "мальчик", "ребёнок", "ребенок",
    "дети", "малыш", "малышка", "дама", "господин", "персонаж", "герой", "принцесса",
    "волшебник", "рыцарь", "фея", "эльф", "санта", "дед", "мороз",
    "бабушка", "бабуля", "баба", "дедушка", "дедуля", "дедуся",
    "старушка", "старик", "девушка", "парень", "юноша", "тётя", "дядя",
    "мама", "папа", "сестра", "брат", "внук", "внучка",
    # russian — animals
    "животное", "питомец", "собака", "кошка", "кот", "пёс", "котёнок", "щенок",
    "кролик", "зайчик", "медведь", "панда", "лев", "тигр", "лиса", "волк",
    "олень", "лошадь", "конь", "слон", "обезьяна", "птица", "сова", "попугай",
    "пингвин", "утка", "цыплёнок", "рыба", "акула", "дельфин", "кит", "лягушка",
    "черепаха", "змея", "хомяк", "крыса", "мышь", "свинья", "корова", "овца",
    "коза", "курица", "дракон", "единорог", "динозавр", "монстр", "существо",
}


# Russian word roots — match any word that STARTS WITH one of these roots
# (handles all declensions: бабушки, бабушку, кошки, кота, дедушке…)
_LIVING_RU_ROOTS = (
    "бабуш", "бабул", "дедуш", "дедул", "дедус", "мальчик", "девочк",
    "девушк", "парен", "юнош", "ребён", "ребен", "малыш", "мужчин",
    "женщин", "тётей", "тёть", "дядей", "дядь", "мамой", "мам", "пап",
    "сестр", "брат", "внук", "тёт", "дяд",
    "кошк", "кошеч", "котёнк", "котик", "котёнок", "котейк", "кот",
    "пёс", "пёсик", "песик", "собак", "собач", "щенк", "щеночек",
    "кролик", "зайч", "зайк", "медвед", "медвеж", "мишк", "мишутк",
    "лисиц", "лисён", "волч", "лошад", "конь",
    "слон", "слонён", "тигрён", "обезьян", "птиц", "птичк", "совы", "совён",
    "попугай", "пингвин", "уточк", "цыплён", "рыбк", "акул", "дельфин",
    "черепах", "лягушк", "ёжик", "ежик", "хомячк",
    "дракон", "дракончик", "единорог", "динозавр", "динозаврик",
)


# English diminutives / pet-talk that won't match a plain singular.
_LIVING_EN_EXTRA = {
    "kitty", "kittie", "doggy", "doggie", "puppy", "birdie", "bunnies",
    "kitties", "doggies", "puppies", "birdies", "froggy", "piggy",
}


def _singularize(word: str) -> str:
    """Cheap English de-pluralization so 'cats'/'puppies' match 'cat'/'puppy'."""
    if len(word) <= 3:
        return word
    if word.endswith("ies"):
        return word[:-3] + "y"
    if word.endswith("es") and word[-3:-2] in {"s", "x", "z", "o", "h"}:
        return word[:-2]
    if word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _has_living_subject(text: str) -> bool:
    """Return True if the text appears to describe a living subject (person or animal)."""
    lower = text.lower()
    words = {w.strip(".,!?;:\"'()«»") for w in lower.split()}
    # Exact English/nominative-Russian keywords (+ English diminutives).
    if words & (_LIVING_KEYWORDS | _LIVING_EN_EXTRA):
        return True
    # English plurals: 'cats', 'dogs', 'puppies' → singular keyword.
    if {_singularize(w) for w in words} & _LIVING_KEYWORDS:
        return True
    # Russian root prefix matching (handles all declined forms + diminutives).
    return any(w.startswith(root) for w in words for root in _LIVING_RU_ROOTS)


# Scale/landscape cues that should resolve to the epic anchor when no living
# subject is present (logic-fix 4.3 — scene_epic was previously unreachable).
_EPIC_SCENE_KEYWORDS = {
    "mountain", "mountains", "ocean", "sea", "canyon", "valley", "desert",
    "glacier", "waterfall", "cliff", "cliffs", "volcano", "fjord", "aurora",
    "galaxy", "cosmos", "horizon", "vast", "epic", "majestic", "panorama",
    "skyline", "storm", "tundra", "iceberg",
    "горы", "гора", "горах", "океан", "море", "каньон", "долина", "пустыня",
    "ледник", "водопад", "скалы", "скала", "вулкан", "космос", "галактик",
    "простор", "ущель", "эпич", "грандиоз", "величеств", "панорам",
}


def _is_epic_scene(text: str) -> bool:
    """Return True for a grand, large-scale scene (no living subject)."""
    lower = text.lower()
    words = {w.strip(".,!?;:\"'()«»") for w in lower.split()}
    if words & _EPIC_SCENE_KEYWORDS:
        return True
    return any(w.startswith(root) for w in words
               for root in ("галактик", "космос", "эпич", "грандиоз", "величеств", "простор"))

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
The user's input may be in Russian, English, or any other language — always output
the scene description in English regardless of the input language.

Write ONLY the scene (45–80 words). Extract concrete visual meaning and drop conversational filler.

Strict rules:
- Output ONLY the scene. NO style words, NO camera/technical words, NO quotes, NO
  explanations — the system adds the visual style and technical quality separately.
- CRITICAL: Describe ONLY what the user explicitly asked for.
  * If the user asks for a vehicle, building, object, landscape, food, or product —
    draw ONLY that subject. Do NOT add any people, characters, or animals UNLESS the user also mentioned them.
  * Only include people/animals if the user explicitly mentions them — translate Russian names/roles:
    бабушка → grandmother, дедушка → grandfather, кошка → cat, собака → dog, etc.
- CRITICAL CHARACTER RULE: If the user mentions a person or character (e.g. "бабушка", "grandma",
  "a girl", "кот", "a cat"), that character MUST appear as the main subject of the scene.
  NEVER drop or replace the requested character with just an object or background.
- If the scene has a LIVING SUBJECT (person, animal, or character), you MUST include ALL of:
  1. A specific facial expression + eye direction (e.g. "warm beaming smile, looking at viewer")
  2. A specific body pose / action (e.g. "arms raised in joyful celebration")
  3. A concrete color palette that fits the mood (e.g. "warm coral and gold tones")
- Infer gender from a name when given. Greetings → warm cheerful mood. Morning wishes →
  calm peaceful mood. Pick a fitting setting for the occasion.
- NEVER scary, never static/blank — characters must feel alive and expressive.
- NEVER use: beautiful, high quality, realistic, perfect, good, nice, 4k, hd, masterpiece,
  Pixar, Disney, Ghibli, DreamWorks.
- COPYRIGHT RULE: If the user names a copyrighted character or franchise, describe it
  with a GENERIC paraphrase of its look (e.g. "a cheerful yellow cartoon sea-sponge
  character") and NEVER write the brand name. NEVER substitute it with a different
  named brand or character — keep the user's own subject, just made generic.
- If a greeting/announcement text is provided, DO NOT draw any letters.
  Instead describe a clean empty area where text can be placed later.
"""

# Путь с фотографией. Сцена «с нуля» здесь вредна: модель получает снимок и
# должна его ПЕРЕРАБОТАТЬ, а не сочинить похожую картинку. Поэтому и жанр
# текста другой — инструкция редактору, а не описание кадра.
_EDIT_SYSTEM = """You write ONE instruction for an image editor that receives a photo of a
real person and must restyle it. The person on the photo must remain the same person.

Write ONLY the instruction (35–70 words), in English, whatever language the user writes in.

Strict rules:
- NEVER describe the person's face, age, hair colour, skin tone, body or gender.
  The editor already has the photo; describing them invites a different-looking person.
  Refer to them only as "the person in the photo".
- Describe ONLY what changes: the setting, the outfit, the lighting, the mood, the camera angle.
- If the user asks for something that would change who the person is (another face,
  another body, a celebrity), describe the setting and outfit instead and leave the person alone.
- NO style words, NO technical words, NO quality words — the system adds those separately.
- NEVER use: beautiful, high quality, realistic, perfect, 4k, hd, masterpiece,
  Pixar, Disney, Ghibli, DreamWorks.
- No letters or text in the image. If a greeting was provided, ask for clean empty space instead.
"""

_CHAT_SYSTEM = """You are Toontoon, a warm, friendly AI assistant that helps people create
beautiful images, postcards and videos.

IMPORTANT: Always reply in English, regardless of the language the user writes in. Keep
the entire product experience in one consistent language.

Your users are mostly people aged 40–60 who are not very tech-savvy. Be simple, kind and
inspiring. Write short sentences.

What you can make:
- Images (1 TOONTOON): Cartoon character, Cute animal, Birds, Fish, Nature, Food
- Postcards (1 TOONTOON): Birthday, Jubilee, Valentine's Day, Wedding, Anniversary,
  Mother's Day, Father's Day, Easter, Thanksgiving, New Year, Graduation, Get Well,
  Just Because, Good Morning, Good Day
- Videos (2 TOONTOON): Animate a photo, Animate a pet, Cartoon character,
  Video greeting, Living nature, Cute animal, Good morning, Inspiring video

When the user describes an idea, suggest the most fitting content type.
Ask 1–2 clarifying questions if needed, then confirm and offer to create it.
Never ask more than 2 questions in a row. Keep replies to at most 3 sentences.

COPYRIGHT RULE: If the user asks for a copyrighted character or franchise (e.g. SpongeBob,
Elsa, Pikachu), do NOT invent a different brand. Either offer a generic look-alike
("a cheerful yellow cartoon sea-sponge character") or gently say it cannot be an exact
brand and propose a generic version. NEVER swap one brand for another brand that the user
did not mention.
"""


# ─── Helpers ─────────────────────────────────────────────────────────────────


# Повторяем только быстрые отказы: лимит запросов, авария на той стороне и
# оборванное соединение возвращаются мгновенно, поэтому вторая попытка почти
# ничего не стоит. Таймаут не повторяем сознательно — он уже съел свои 20
# секунд, и второй заход рискует упереться в таймаут прокси, оставив человека
# вообще без ответа.
_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
_RETRY_PAUSE_SECONDS = 1.0


async def _call(messages: list[dict], *, max_tokens: int = 300, temperature: float = 0.7) -> str:
    """Make a chat completion call and return the assistant's text.

    Retries once on a fast, transient failure. Падение сюда стоит дорого: без
    промпта запрос либо отменяется, либо собирается механически, поэтому одна
    дешёвая пересдача окупается.
    """
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
        for attempt in (1, 2):
            try:
                resp = await client.post(_OPENAI_CHAT_URL, headers=headers, json=payload)
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                if attempt == 2 or exc.response.status_code not in _RETRY_STATUSES:
                    raise
                log.warning("OpenAI HTTP %s — повтор", exc.response.status_code)
            except (httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError) as exc:
                if attempt == 2:
                    raise
                log.warning("OpenAI соединение оборвалось (%s) — повтор", type(exc).__name__)
            else:
                return resp.json()["choices"][0]["message"]["content"].strip()
            await asyncio.sleep(_RETRY_PAUSE_SECONDS)
    raise RuntimeError("unreachable")  # pragma: no cover


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
    editing: bool = False,
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

    # Neutralize named third-party IP once, up front, so the same normalized
    # subject feeds both the template and the free-scene paths (logic-fix 3.2).
    free_text = prompt_style.neutralize_ip(free_text)

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
    elif editing:
        # Со снимком человека мультяшный якорь выбирать нельзя: «живой субъект»
        # на фотографии — это всегда человек, и автовыбор уводил бы каждое фото
        # в мультик независимо от того, что обещал экран.
        style_key = "realistic"
    else:
        # Auto-select anchor: living subjects → 3d_cartoon; objects/scenes → scene_cozy.
        # This prevents the character-design anchor from biasing inanimate generations
        # (e.g. "Porsche car" would otherwise get "cheerful charming character design").
        combined_text = " ".join(filter(None, [free_text, tile.title if tile else ""]))
        if _has_living_subject(combined_text):
            style_key = "3d_cartoon"
        elif _is_epic_scene(combined_text):
            style_key = "scene_epic"
        else:
            style_key = "scene_cozy"
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

    user_msg = "\n".join(parts) or "A warm, friendly greeting image"

    messages = [
        {"role": "system", "content": _EDIT_SYSTEM if editing else _SCENE_SYSTEM},
        {"role": "user", "content": user_msg},
    ]
    try:
        scene = await _call(messages, max_tokens=160)
    except Exception:
        return "", ""

    if not scene:
        return "", ""
    prompt = prompt_style.assemble(
        scene, style_key=style_key, is_text=is_text, editing=editing
    )
    log.info("[build_prompt] style=%s → key=%s editing=%s | scene: %s",
             style, style_key, editing, scene[:120])
    log.info("[build_prompt] final_prompt: %s", prompt[:200])
    return prompt, prompt_style.NEGATIVE_PROMPT


# ─── Role 2: Chat assistant ──────────────────────────────────────────────────


async def chat_reply(
    *,
    message: str,
    history: list[dict],
) -> str:
    """Return Toontoon's reply to a user message in the chat.

    ``history`` is a list of ``{"role": "user"|"assistant", "content": str}``.
    Falls back to a static reply if OpenAI is not configured.
    """
    if not settings.openai_enabled:
        return "Hi! I'm Toontoon. What would you like to create — a postcard, an image, or a video?"

    messages = [{"role": "system", "content": _CHAT_SYSTEM}]
    messages.extend(history[-10:])  # Keep last 10 turns for context.
    messages.append({"role": "user", "content": message})

    try:
        return await _call(messages, max_tokens=200)
    except Exception:
        return "Sorry, I'm having trouble right now. Please try again in a moment."
