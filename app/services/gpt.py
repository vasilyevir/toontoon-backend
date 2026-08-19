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
import base64
import json
import logging
import re
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
from app.storage import images as storage_images

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
"""

# Что дописывается к инструкции в зависимости от того, что человек делает.
#
# Раньше запрет на буквы стоял в самой инструкции, всегда. Для портрета это
# правильно — подпись в углу кадра там мусор. Для постера это отмена постера:
# человек просил слова, а мы просили пустое место, и модель честно рисовала
# сцену. Так «постер с моим именем и словами про баскетбол» превратился в
# фотографию баскетболиста на площадке.
_NO_LETTERS = (
    "- No letters or text in the image. If a greeting was provided, ask for "
    "clean empty space instead.\n"
)

_LETTERING = (
    "- This is a POSTER, and a poster lives by its lettering. The words the "
    "person gave MUST appear in the image, spelled exactly as they wrote them, "
    "set in large display type as the loudest thing in the frame.\n"
    "- Compose a poster, not a photograph of a scene: flat graphic shapes and "
    "blocks of colour, generous margins, a clear hierarchy of headline, subject "
    "and supporting words.\n"
    "- The subject stands against those shapes, not inside a real place. Do not "
    "invent a location, a venue or an activity the person did not ask for.\n"
    "- The person is cut out of their photo: whatever room, wall or furniture "
    "was around them is gone. Never describe keeping, widening or extending "
    "the place they were photographed in.\n"
)


def _system_for(*, editing: bool, lettering: bool) -> str:
    """Инструкция сборщику: что писать и можно ли писать буквы."""
    base = _EDIT_SYSTEM if editing else _SCENE_SYSTEM
    return base + (_LETTERING if lettering else _NO_LETTERS)

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


def _content_of(payload: dict) -> str:
    """Текст ответа — или пустая строка, если его нет.

    Пустой ответ приходит штатно: модели с рассуждением возвращают
    `content: null`, потратив весь лимит на размышление, и это не сбой сети, а
    «ничего не сказал». Раньше здесь звался `.strip()` у пустоты — разбор фразы
    отвечал приложению пятисоткой, а сборка промпта уходила в отказ с возвратом
    TOONTOON. Оба места умеют работать с пустым ответом, и им нужно дать его, а
    не исключение.
    """
    choices = payload.get("choices") or [{}]
    message = choices[0].get("message") or {}
    return (message.get("content") or "").strip()


def _model_for(asked: str | None, *, use_router: bool) -> str:
    """Какая модель поедет в запрос.

    Просьба о конкретной модели выполняется только через витрину: `asked` — это
    её идентификатор вида `google/gemini-2.5-flash`, и прямому OpenAI он не
    годится, там такой модели просто нет. Без ключа витрины просьба тихо
    отменяется — разбор уйдёт на общую модель и сработает хуже, но сработает.
    Отвечать отказом на то, что модель разбора выбрана в настройках, а витрина
    не подключена, было бы наказанием за чужую настройку.
    """
    if use_router:
        return asked or settings.openrouter_text_model
    return settings.openai_model


async def _call(messages: list[dict], *, max_tokens: int = 300, temperature: float = 0.7,
                usage: dict | None = None, model: str | None = None) -> str:
    """Make a chat completion call and return the assistant's text.

    Retries once on a fast, transient failure. Падение сюда стоит дорого: без
    промпта запрос либо отменяется, либо собирается механически, поэтому одна
    дешёвая пересдача окупается.

    В `usage` витрина складывает расход токенов, если зовущий его считает. Нужно
    это замеру моделей: у моделей с рассуждением ответ короткий, а счёт длинный —
    оплачивается и то, что они думали. Считать такую цену по длине промпта
    значит сравнивать их с обычными моделями по чужой ставке.
    """
    # Витрина, если ключ есть; прямой OpenAI — как было. Выбор здесь, а не в
    # настройке маршрута, потому что это одна и та же операция, и звать её
    # по-разному в зависимости от вендора незачем.
    use_router = bool(settings.openrouter_api_key)
    url = (f"{settings.openrouter_base_url.rstrip('/')}/chat/completions"
           if use_router else _OPENAI_CHAT_URL)
    headers = {
        "Authorization": f"Bearer "
                         f"{settings.openrouter_api_key if use_router else settings.openai_api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": _model_for(model, use_router=use_router),
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    async with httpx.AsyncClient(timeout=20) as client:
        for attempt in (1, 2):
            try:
                resp = await client.post(url, headers=headers, json=payload)
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
                body = resp.json()
                if usage is not None:
                    usage.update(body.get("usage") or {})
                return _content_of(body)
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


# Назначения, которые живут надписью. Постер без букв — это картинка, а
# открытка без поздравления — просто рисунок.
LETTERING_INTENTS = frozenset({"poster", "card"})


async def build_prompt(
    *,
    tile: Optional[Tile],
    answers: dict[str, str],
    free_text: Optional[str],
    style: Optional[str],
    editing: bool = False,
    intent: Optional[str] = None,
    style_ref: bool = False,
    redraw: bool = False,
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

    lettering = (intent or "").strip().lower() in LETTERING_INTENTS
    system = _system_for(editing=editing, lettering=lettering)
    if style_ref:
        # Иначе сборщик пересказывает стиль словами — «в стиле как на образце»,
        # — и модель получает описание вместо самого образца, который у неё уже
        # есть перед глазами.
        system += (
            "- A style sample is attached. Do not describe its look in words: "
            "the editor can see it. Describe only the subject and what happens "
            "in the frame.\n"
        )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_msg},
    ]
    try:
        scene = await _call(messages, max_tokens=160)
    except Exception:
        return "", ""

    if not scene:
        return "", ""
    prompt = prompt_style.assemble(
        scene, style_key=style_key, is_text=is_text, editing=editing,
        lettering=lettering, style_ref=style_ref, redraw=redraw,
    )
    log.info("[build_prompt] style=%s → key=%s editing=%s | scene: %s",
             style, style_key, editing, scene[:120])
    log.info("[build_prompt] final_prompt: %s", prompt[:200])
    # Запрет на буквы снимается ровно там, где буквы и заказаны.
    return prompt, prompt_style.negative_for(style_key, lettering=lettering)


# ─── Role 2: Chat assistant ──────────────────────────────────────────────────


def chat_directive(ask_about: str | None, known: dict[str, str] | None = None,
                   *, photo_attached: bool = False) -> str:
    """Указание модели: о чём спросить в этот раз и о чём не спрашивать.

    Выбор темы вопроса — решение, а не формулировка, и принимается оно кодом
    (`conversation.next_gap`). Модели остаётся сказать это словами: она пишет
    лучше, а решает хуже — сама она спрашивала про персонажа, пока пропорции,
    снимок человека и половина сказанного оставались за бортом.
    """
    lines = []
    if photo_attached:
        # Снимок приходит картинкой, а модель читает только текст: в истории на
        # его месте пусто. Без этой строки она отвечала «фотографии не вижу»
        # человеку, который её только что приложил и видит в переписке.
        lines.append("The person has attached their photo — it is in this request. "
                     "Never say you cannot see it and never ask for it again.")
    if known:
        # Перечисление сказанного нужнее запрета: «не спрашивай про стиль»
        # модель прочитает и всё равно спросит, а список уже известного она
        # пересказывает своими словами и на том успокаивается.
        said = "; ".join(f"{slot}: {value}" for slot, value in sorted(known.items()))
        lines.append(f"The person has already said — {said}. Treat all of it as settled: "
                     "never ask about any of it again, and do not ask them to confirm it.")
    if ask_about is None:
        lines.append("Nothing essential is missing. Do not ask another question: say in one "
                     "sentence what you understood and offer to create it.")
    else:
        lines.append(f"Ask about exactly one thing — {ask_about} — and nothing else. "
                     "It is the one answer that changes the picture most right now.")
    return "\n".join(lines)


async def chat_reply(
    *,
    message: str,
    history: list[dict],
    ask_about: str | None = None,
    known: dict[str, str] | None = None,
    photo_attached: bool = False,
) -> str:
    """Return Toontoon's reply to a user message in the chat.

    ``history`` is a list of ``{"role": "user"|"assistant", "content": str}``.
    Falls back to a static reply if OpenAI is not configured.

    ``ask_about`` и ``known`` — что спросить и что уже известно. Без них модель
    выбирает вопрос сама, и выбирает плохо: свободный чат так ни разу не спросил
    про пропорции и дважды вернулся к стилю, названному первой же фразой.
    """
    if not settings.openai_enabled:
        return "Hi! I'm Toontoon. What would you like to create — a postcard, an image, or a video?"

    # Указание стоит после истории, а не до неё: модель тем сильнее слушает, чем
    # ближе к концу написано. Стоя первым, оно проигрывало разговору — человек
    # уже ответил про формат, а она спрашивала о нём снова, потому что двадцатью
    # репликами выше этот вопрос звучал.
    messages = [{"role": "system", "content": _CHAT_SYSTEM}]
    messages.extend(history[-10:])  # Keep last 10 turns for context.
    messages.append({"role": "system", "content": chat_directive(
        ask_about, known, photo_attached=photo_attached)})
    messages.append({"role": "user", "content": message})

    try:
        return await _call(messages, max_tokens=200)
    except Exception:
        return "Sorry, I'm having trouble right now. Please try again in a moment."


# ─── Разбор сказанного по слотам ─────────────────────────────────────────────

# Ответ — короткий JSON, и двухсот токенов на него с запасом. У моделей с
# рассуждением этот же лимит уходит на размышление целиком, и они возвращают
# пустоту; поднимать его ради них в проде незачем, но замер по моделям должен
# уметь это проверить — отсюда константа, а не число в вызове.
EXTRACT_MAX_TOKENS = 200

_EXTRACT_SYSTEM = (
    "You read a message from a person describing an image they want, and "
    "report which of the given slots that message already covers. The message "
    "may be one word or several sentences, and it may cover several slots at "
    "once — report every slot it covers, not just the first.\n"
    "Rules:\n"
    "- Only report a slot if the message really says something about it. "
    "Guessing is worse than leaving it empty: an unasked question is a small "
    "annoyance, a wrong answer is a wrong picture.\n"
    "- A slot marked `one of:` is a choice, not a description. Answer it with "
    "exactly one of the listed ids, copied character for character, and only "
    "when the message clearly points at that one. Anything else is discarded, "
    "so an id you had to stretch to reach is the same as no answer at all.\n"
    "- Every other slot is free text, and its value MUST be in English — every "
    "single one of them, however short the person's message was and whatever "
    "language they wrote it in. Translate their wording, never copy it "
    "verbatim: everything downstream is English, and a foreign phrase reaching "
    "the image model produces a picture that has nothing to do with the "
    "request.\n"
    "- `text` is the one and only slot copied in the original language: those "
    "are the words that must appear in the image, so they keep the person's "
    "spelling and case exactly. That exception applies to `text` and to no "
    "other slot.\n"
    "- Report `text` only when the person gives the words themselves. The "
    "reason for a card is its occasion, not its caption: «a New Year card» "
    "asks for no lettering at all, and printing those three words across the "
    "picture is not what was asked.\n"
    "- «Doesn\'t matter», «anything», «whatever», «you decide» is a refusal to "
    "answer, not an answer. Leave the slot out. Writing «not important» into "
    "it records their shrug as their wish, and the picture then obeys it.\n"
    "- What they are making — a poster, a card, a portrait — describes no slot "
    "by itself. A poster is not a shape, a card is not a caption, a portrait "
    "is not a framing. Report what the words say, not what the genre usually "
    "looks like.\n"
    "- Keep it to their meaning. Do not invent detail they did not give.\n"
    "- Answer with a JSON object only: {\"slot\": \"value\", ...}. "
    "Empty object if nothing is covered."
)

# Что означает каждый слот — словами, а не именем поля: модель читает описание,
# а не догадывается по идентификатору.
SLOT_MEANING: dict[str, str] = {
    "technique": "the drawing technique the picture is made in — the look itself, not the subject",
    "format": (
        "the shape of the frame — tall, square, wide — and only when they name "
        "it or say where it will be shown. NOT how close the camera is, and not "
        "something a poster, a cover or a wallpaper implies on its own"
    ),
    "place": "where the scene happens",
    "light": "the light source or time of day",
    "wardrobe": "what the person wears",
    "framing": "how close the camera is — close-up, waist-up, full body. NOT the orientation of the frame",
    "palette": "colours to use",
    "text": "words that must appear in the image",
    "occasion": "the reason for a greeting card",
    "product": "the object shown together with the person",
    # «Как будто работа для Behance», «спортивная стилистика как для NBA» — это
    # не техника и не место, а мир, из которого картинка. Без своего поля такое
    # оставалось только в первой фразе и растворялось в ней целиком.
    "reference": (
        "a genre, publication, campaign or scene the picture should feel like — "
        "the world it comes from, NOT the drawing technique"
    ),
}

# Слоты, у которых значение уезжает не в текст промпта, а в параметр: якорь
# стиля и пропорции сервер сверяет со своим набором, и свободное «горизонтально»
# он молча отбросит.
#
# Раньше из-за этого их тут не было вовсе — и человек, написавший «постер в
# стиле аниме», всё равно получал вопрос «как это должно выглядеть?» с кнопкой
# «Anime» среди прочих. Спрашивать о сказанном — ровно то, чего разбор и должен
# избегать; закрытый список этому не мешает, если модель не описывает, а
# выбирает. Поэтому варианты перечислены здесь, ответ сверяется с ними ниже, и
# всё, чего в списке нет, отбрасывается — промахнуться значением нельзя.
SLOT_OPTIONS: dict[str, dict[str, str]] = {
    "technique": {
        "realistic": "a photograph, photorealistic",
        "scene_epic": "cinematic, dramatic, epic scale",
        "scene_cozy": "cosy, warm, small and homely",
        "3d_cartoon": "3D cartoon, animated feature film look",
        "semi_real_3d": "stylised 3D with lifelike proportions",
        "anime": "anime or manga illustration",
    },
    "format": {
        "9:16": "tall vertical, full phone screen",
        "4:5": "portrait, slightly taller than wide",
        "1:1": "square",
        "16:9": "wide landscape, horizontal",
    },
}


def _slot_line(slot: str) -> str:
    """Строка про один слот для модели: описание, а у закрытых — ещё и выбор."""
    options = SLOT_OPTIONS.get(slot)
    if not options:
        return f"- {slot}: {SLOT_MEANING[slot]}"
    choices = "; ".join(f"{key} = {hint}" for key, hint in options.items())
    return f"- {slot}: {SLOT_MEANING[slot]}. One of: {choices}"


# Отговорка, записанная в поле, — то же враньё, только вежливое: «не важно» в
# графе одежды уедет в картинку требованием. Модели на этом спотыкаются каждая
# по-своему и в среднем раз на двадцать фраз; ловить это списком слов дешевле и
# надёжнее, чем очередной правкой промпта, — перебор здесь невозможен, потому
# что ни одна из этих фраз не является описанием чего бы то ни было.
_SHRUG_RE = re.compile(
    r"^(any|anything|whatever|none|nothing|n/?a|unspecified|unknown|"
    r"not (important|specified|mentioned|given|stated)|"
    r"doesn'?t matter|no preference|your choice|you decide|up to you)\b",
    re.IGNORECASE,
)


# Название жанра, положенное в слот вместо описания: на «хочу портрет в кофейне»
# модели пишут в кадрирование `portrait`, на «постер» — `poster` в референс.
# Это эхо первого слова, а не ответ: человек назвал, что делает, а не как это
# снято. Одним словом описания не бывает ни у одного из свободных слотов, и
# правило в промпте моделей не удержало — держим здесь.
_GENRE_ECHO_RE = re.compile(
    r"^(a |an |the )?(portrait|poster|card|greeting card|postcard|product|"
    r"cover|banner|picture|image|photo)$",
    re.IGNORECASE,
)


def _clean_slot_value(slot: str, value: str) -> str:
    """Значение, годное к отправке, или пустая строка.

    У закрытых слотов годится только идентификатор из списка: всё остальное
    приложение показать не сможет, а сервер не примет. Регистр и лишние пробелы
    прощаем — это оплошность модели, а не другой ответ.

    У свободных выбрасывается отговорка — «не важно что надето» есть отказ
    отвечать, и записать его в поле значит принять пожатие плечами за пожелание, —
    и эхо жанра: `portrait` в кадрировании это первое слово фразы, а не ответ на
    вопрос «насколько близко».
    """
    text = value.strip()
    options = SLOT_OPTIONS.get(slot)
    if options is None:
        if _SHRUG_RE.match(text) or _GENRE_ECHO_RE.match(text):
            return ""
        return text
    for key in options:
        if text.lower() == key.lower():
            return key
    return ""


def extract_messages(text: str, wanted: list[str]) -> list[dict]:
    """Ровно то, что уезжает в модель на разборе.

    Вынесено из `extract_slots`, чтобы замер по моделям считал токены и цену по
    настоящему запросу. Пересобранная в скрипте копия разошлась бы с этой на
    первой же правке промпта, и сравнение моделей молча стало бы сравнением
    двух разных задач.
    """
    listing = "\n".join(_slot_line(s) for s in wanted)
    return [
        {"role": "system", "content": _EXTRACT_SYSTEM},
        {"role": "user", "content": f"Slots:\n{listing}\n\nMessage: {text}"},
    ]


async def extract_slots(text: str, slots: list[str], *,
                        usage: dict | None = None) -> dict[str, str]:
    """Что из перечисленного человек уже сказал.

    Разбор по спискам слов промахивался на первом же перефразе: «сделай
    потеплее» — это про свет, «в кофейне» — про место, а подстроки этого не
    видят. Здесь фразу читает модель, и стоит это сотые доли цента против
    четырёх центов за кадр.

    Пустой словарь — законный ответ и самый частый: человек обычно говорит про
    одно. Отказ модели тоже даёт пустой словарь, и разговор просто задаёт свои
    вопросы, как задавал бы без разбора вовсе.
    """
    wanted = [s for s in slots if s in SLOT_MEANING]
    if not text.strip() or not wanted:
        return {}

    try:
        raw = await _call(
            extract_messages(text, wanted),
            max_tokens=EXTRACT_MAX_TOKENS,
            temperature=0,
            usage=usage,
            model=settings.slot_extraction_model or None,
        )
    except Exception:  # noqa: BLE001 — разбор необязателен, разговор обязателен
        # Докстринг обещал это с самого начала, а код обещания не держал: вызов
        # стоял вне try, и оборванная сеть поднималась наружу. В чате её никто
        # не ловил — ход отвечал пятисоткой, и обе реплики пропадали, хотя
        # человеку достаточно было задать вопрос, как без разбора вовсе.
        return {}
    try:
        start, end = raw.index("{"), raw.rindex("}") + 1
        parsed = json.loads(raw[start:end])
    except (ValueError, json.JSONDecodeError):
        return {}
    filled = {}
    for key, value in parsed.items():
        if key not in wanted:
            continue
        cleaned = _clean_slot_value(key, str(value))
        if cleaned:
            filled[key] = cleaned
    return filled

# ─── Что делать дальше с готовым кадром ──────────────────────────────────────

_IDEAS_SYSTEM = (
    "You are shown the prompt that produced a picture, and you propose what to "
    "change next. Your reader is not a designer: the hardest part for them is "
    "not tapping, it is knowing what to ask for.\n"
    "Rules:\n"
    "- Write exactly four ideas, one per line, nothing else. No numbering, no "
    "bullets, no quotes, no headings.\n"
    "- Each line is a finished instruction the person could send as it is, "
    "addressed to you: «Swap the palette for neon blue and electric violet», "
    "«Drop the background and leave flat colour blocks».\n"
    "- Eight to sixteen words. Concrete and visual: name the colour, the light, "
    "the outfit, the composition. «Make it better» is not an idea.\n"
    "- Each one changes something different. Four shades of the same change is "
    "one idea and three wasted lines.\n"
    "- Stay on this picture. Do not propose what it already has, and do not "
    "propose a different picture altogether.\n"
    "- English, plain and warm. Never name a studio or a brand.\n"
)


async def next_step_ideas(*, prompt: str, intent: str | None = None) -> list[str]:
    """Четыре готовые правки к кадру.

    Пустой список — законный ответ: модель отказала или промпта нет. Приложение
    тогда показывает обычные кнопки уточнений, и человек не видит поломки.
    """
    if not settings.openai_enabled or not prompt.strip():
        return []

    what = f"This is a {intent}." if intent else ""
    try:
        raw = await _call(
            [
                {"role": "system", "content": _IDEAS_SYSTEM},
                {"role": "user", "content": f"{what}\nThe picture was made with:\n{prompt}"},
            ],
            max_tokens=260,
            temperature=0.7,
            model=settings.slot_extraction_model or None,
        )
    except Exception:  # noqa: BLE001 — без идей экран живёт, без кадра нет
        return []

    ideas = []
    for line in raw.splitlines():
        # Модель то нумерует, то ставит маркеры, сколько ни проси. Снимаем это
        # здесь, а не очередной строкой в промпте: правило, которое можно
        # выполнить кодом, в промпте только занимает внимание.
        cleaned = line.strip().lstrip("-•*0123456789.） )").strip().strip('"').strip()
        if len(cleaned) > 3:
            ideas.append(cleaned)
    return ideas[:4]


# ─── Кто на приложенных снимках ──────────────────────────────────────────────

_ROLES_SYSTEM = (
    "You look at the pictures a person attached to an image request and say "
    "what each one is FOR. Two roles exist:\n"
    "- `person`: a photograph of the human (or pet) who must appear in the "
    "result. Their face has to survive into the picture.\n"
    "- `style`: a poster, an artwork, a screenshot, a design — attached as a "
    "sample to copy the look from. Whoever is drawn on it is a stranger and "
    "must NOT appear in the result.\n"
    "Rules:\n"
    "- Answer with a JSON array of roles, one per picture, in the order given: "
    "[\"person\", \"style\"]. Nothing else.\n"
    "- A plain photograph of somebody — at home, outdoors, a selfie, a portrait "
    "— is `person`. Lettering, layout, logos, borders, a designed composition, "
    "or an obviously illustrated character mean `style`.\n"
    "- When a picture could be either, read the person's message: they usually "
    "say which one is theirs and which one is the sample.\n"
    "- Never answer `style` for every picture: something has to be the subject "
    "unless the message asks for a picture with nobody in it.\n"
)


async def reference_roles(images: list[tuple[bytes, str]], message: str) -> list[str]:
    """Что из приложенного — человек, а что образец стиля.

    Спрашивается у модели, а не у человека. Он прикладывает снимок и постер и
    пишет «сделай меня в стилистике этого» — по его словам роли ясны, по
    порядку файлов нет, а требовать от него расставить их руками значит
    требовать понимать нашу механику. Отличить портрет от постера модель умеет
    надёжно, и это дешевле любой ошибки: перепутанные роли — это чужое лицо в
    кадре вместо своего.

    Пустой список — «не знаю»: тогда всё остаётся как прислали. Ошибиться
    молчанием безопаснее, чем догадкой.
    """
    if not settings.openai_enabled or len(images) < 2:
        return []

    parts: list[dict] = [{
        "type": "text",
        "text": f"The person wrote: {message.strip() or '(nothing)'}\n"
                f"There are {len(images)} pictures, in order.",
    }]
    for data, mime in images:
        small = base64.b64encode(storage_images.preview(data)).decode()
        parts.append({"type": "image_url",
                      "image_url": {"url": f"data:image/jpeg;base64,{small}"}})

    try:
        raw = await _call(
            [{"role": "system", "content": _ROLES_SYSTEM},
             {"role": "user", "content": parts}],
            max_tokens=60,
            temperature=0,
            model=settings.slot_extraction_model or None,
        )
        start, end = raw.index("["), raw.rindex("]") + 1
        roles = json.loads(raw[start:end])
    except Exception:  # noqa: BLE001 — не разобрали, значит оставляем как есть
        return []

    roles = [r if r in ("person", "style") else "person" for r in roles]
    if len(roles) != len(images) or "person" not in roles:
        return []
    return roles
