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

# Фотография человека плюс образец стиля.
#
# Общая инструкция редактору здесь вредит дважды. Она требует описать
# «обстановку, свет и настроение» и уложиться в 35–70 слов — а человек сказал
# всего три слова: «в такой же стилистике». Сборщик честно добирал недостающее
# из головы: на постер AKAI он написал лес с белкой, на второй заход — парк в
# золотой час. Ни того, ни другого никто не просил.
#
# Второй вред тише: свет и палитра принадлежат образцу, который у редактора
# перед глазами. Описанные словами, они с ним спорят — и выигрывают слова.
_EDIT_WITH_SAMPLE = """You write ONE short instruction for an image editor. The editor receives a
photo of a real person AND a style sample image, and must redraw the person in the
sample's style. The person must remain the same person.

Write ONLY the instruction, in English, whatever language the user writes in.
Be brief — one or two sentences. Saying less is better than filling space.

Strict rules:
- NEVER describe the person's face, age, hair colour, skin tone, body or gender.
  The editor already has the photo. Refer to them only as "the person in the photo".
- The style sample owns the look: its palette, its lighting, its background and the way
  it composes its subject. NEVER put any of that into words — the editor can see it.
- Describe ONLY what the user actually asked for. If they asked for nothing beyond
  "make me in this style", then say only that the person is redrawn in the sample's
  style, framed and composed the way the sample frames its own subject.
- NEVER invent a setting, a season, a time of day, weather, props, animals, an
  activity, a pose or an expression that the user did not ask for. An empty answer
  from the user is not a gap to fill.
- If the user DID name a subject or a theme (a sport, a place, an object, an
  occasion), keep it and describe only that.
- NO style words, NO technical words, NO quality words.
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
    # Условие названо с обеих сторон намеренно. «Если поздравление дали,
    # попроси пустое место» сборщик читает как разрешение написать эту фразу
    # всегда — и на просьбу «сделай меня в такой же стилистике», где никакого
    # поздравления не было, в промпт уезжало «Please provide clean empty
    # space». Пустое место посреди кадра никто не заказывал.
    "- No letters or text in the image. Only if a greeting text was provided, "
    "describe a clean empty area for it; if none was given, say nothing about "
    "empty space at all.\n"
)

# Просьба про буквы вне постера: надпись нужна, а плакатная вёрстка — нет.
#
# «Вместо akai напиши моё имя» — это заказ надписи в той же картинке, а не
# заказ плаката: композицию человек уже показал образцом, и переверстывать её
# по нашим правилам значит не сделать то, о чём просили.
_LETTERING_PLAIN = (
    "- The words the person gave MUST appear in the image, spelled exactly as "
    "they wrote them, set as real lettering large enough to read at a glance.\n"
    "- Do not invent extra words, captions or slogans they did not ask for.\n"
)

_POSTER_NO_WORDS = (
    "- This is a POSTER, but no words were given. Compose it with clean empty "
    "space where a headline would go, and do NOT invent any lettering: no "
    "words, no captions, no slogans, no signage.\n"
    "- Compose a poster, not a photograph of a scene: flat graphic shapes and "
    "blocks of colour, generous margins, the subject against those shapes.\n"
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


# Когда кадр не правят, а перерисовывают.
#
# Инструкция редактору написана про правку: «опиши только то, что меняется».
# Для фотографии это верно. Для аниме — нет: меняется всё, кроме того, кто этот
# человек. Со словами «change the setting, update the outfit» модель послушно
# возвращала ту же фотографию с другим фоном — человек прикладывал аниме-постер
# и получал себя же на закатной улице.
_REDRAW_NOTE = (
    "- The output is a NEW drawing of this person, not a retouched photograph. "
    "Describe the finished picture — where they are, what they wear, what they "
    "are doing — as if it is being drawn from scratch. Do not write «change», "
    "«update» or «keep»: nothing from the photograph survives except who they "
    "are.\n"
    "- Call them «the character», never «the person in the photo»: that phrase "
    "points the editor back at the photograph, and it obeys — the face comes "
    "out photographic on a drawn background.\n"
)


def _system_for(*, editing: bool, lettering: bool, poster: bool = False,
                drawn: bool = False, style_ref: bool = False) -> str:
    """Инструкция сборщику: что писать и можно ли писать буквы.

    Буквы и постер — разные вещи. Постер живёт вёрсткой: плоские формы, поля,
    иерархия. Просьба «напиши здесь моё имя» — это только надпись, и навязывать
    ей плакатную вёрстку значит переделать картинку, которую человек уже
    показал образцом.
    """
    if editing:
        # Образец меняет саму задачу: описывать нужно не «что изменится», а
        # только то, что человек назвал сверх самого образца — чаще всего
        # ничего.
        base = _EDIT_WITH_SAMPLE if style_ref else _EDIT_SYSTEM
    else:
        base = _SCENE_SYSTEM
    if drawn and editing:
        base += _REDRAW_NOTE
    if poster:
        # Слов нет — постер собирается с чистым местом под заголовок. Требовать
        # надпись, не сказав какую, значит просить модель придумать слова, и она
        # придумывает: набирает то, что видит, вплоть до самой просьбы.
        return base + (_LETTERING if lettering else _POSTER_NO_WORDS)
    return base + (_LETTERING_PLAIN if lettering else _NO_LETTERS)

# Цены подставляются из настроек, а не набраны прозой.
#
# Набранные руками, они разъезжаются с кошельком молча: тариф правится в
# конфиге, промпт остаётся прежним, и ассистент называет вчерашнюю цену с
# полной уверенностью. Замер показал и худшее — на «Сколько это стоит?» он
# отвечал «my services are free» и «I don't have specific pricing information»
# в трёх случаях из четырёх. Про деньги нельзя догадываться вслух.
_CHAT_SYSTEM_TEMPLATE = """You are Toontoon, a warm, friendly AI assistant that helps people create
beautiful images and postcards.

IMPORTANT: Reply in the language the person writes in. They wrote to you in their own
words; answering in another language is answering somebody else. If their message is too
short to tell (a number, an emoji, "ok"), keep the language of the conversation so far.

This applies only to what the person reads. Everything the picture generator receives
stays English — that is machinery, and the quality of the frame depends on it.

Your users are mostly people aged 40–60 who are not very tech-savvy. Be simple, kind and
inspiring. Write short sentences.

In languages that have a polite and a familiar form of "you", always use the polite one
(Russian «вы», German "Sie", French "vous"). Two replies in a row said «ты» and then
«вы» to the same person — a stranger who suddenly starts addressing you familiarly reads
as careless, and this audience notices.

What you can make:
- Images ({image_cost} TOONTOON): portraits, posters, scenes — anything they describe
- Postcards ({image_cost} TOONTOON): birthdays, holidays, anniversaries, good wishes

PRICE RULE: an image or a postcard costs {image_cost} TOONTOON. Never invent a price,
never say the service is free, and never say you do not know what it costs. If asked
about money, give exactly this number.

VIDEO RULE: you cannot make video yet. If they ask for one, say plainly in one
sentence that video is coming soon and offer to make a picture meanwhile. Do not ask
what kind of video they want, do not describe what the video would look like, and
never name a price for it. Saying «sure, what kind of video?» and then handing them a
still picture is worse than saying «not yet».
A video request is not always called «video»: «animate my photo», «оживить фото»,
«make it move», «сделай гифку» are all video. Read what they want, not the word.

TOONTOON is the name of our currency and is always written exactly like that, in
Latin letters, in every language. Never transliterate it («ТОНТОН») and never
translate it.

When the user describes an idea, suggest the most fitting content type.
Ask 1–2 clarifying questions if needed, then confirm and offer to create it.
Never ask more than 2 questions in a row. Keep replies to at most 3 sentences.

COPYRIGHT RULE: If the user asks for a copyrighted character or franchise (e.g. SpongeBob,
Elsa, Pikachu), do NOT invent a different brand. Either offer a generic look-alike
("a cheerful yellow cartoon sea-sponge character") or gently say it cannot be an exact
brand and propose a generic version. NEVER swap one brand for another brand that the user
did not mention.
"""

_CHAT_SYSTEM = _CHAT_SYSTEM_TEMPLATE.format(
    image_cost=settings.image_toontoon_cost,
)


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
    sample_brands: list[str] | None = None,
    redraw: bool = False,
    subject: str = "person",
    cast: list[str] | None = None,
    lettering: bool | None = None,
    poster: bool | None = None,
    lettering_text: str | None = None,
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
    if lettering_text:
        # Дословно и отдельной строкой: в общей просьбе слова для надписи
        # теряются среди всего остального, а набрать их надо ровно так, как
        # человек написал.
        parts.append(f'The exact words to set in the picture: "{lettering_text}". '
                     "No other words.")

    user_msg = "\n".join(parts) or "A warm, friendly greeting image"

    # Буквы решает вызывающий: он видит и слова человека, и назначение. Здесь
    # остаётся умолчание для старых путей, которые про надпись ничего не знают.
    if poster is None:
        poster = (intent or "").strip().lower() in LETTERING_INTENTS
    if lettering is None:
        lettering = poster and bool(lettering_text)
    system = _system_for(editing=editing, lettering=lettering, poster=poster,
                         drawn=prompt_style.is_drawn(style_key), style_ref=style_ref)
    if style_ref and not editing:
        # Путь без фотографии: сцену всё-таки пишем, но образец уже отвечает за
        # вид. Правило про палитру называется по номеру намеренно — общий
        # список требует её «конкретной», и мягкая приписка этому проигрывает,
        # как проигрывала счётчику слов приписка на пути с фотографией.
        system += (
            "- A style sample is attached. Do not describe its look in words: "
            "the editor can see it. Describe only the subject and what happens "
            "in the frame, and IGNORE the rule about naming a concrete colour "
            "palette — the sample owns the palette.\n"
            "- If the person did not say what should happen, do NOT invent a "
            "setting, props, animals or an activity: keep the frame to the "
            "subject and let the sample decide the rest.\n"
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
        poster=poster, style_ref=style_ref, sample_brands=sample_brands, redraw=redraw,
        subject=subject, cast=cast,
    )
    log.info("[build_prompt] style=%s → key=%s editing=%s | scene: %s",
             style, style_key, editing, scene[:120])
    log.info("[build_prompt] final_prompt: %s", prompt[:200])
    # Запрет на буквы снимается ровно там, где буквы и заказаны.
    return prompt, prompt_style.negative_for(style_key, lettering=lettering)


# ─── Role 2: Chat assistant ──────────────────────────────────────────────────


# Слова, которыми просят себя в кадре. Список короткий и намеренно грубый:
# ошибка здесь стоит одной лишней подстановки снимка, который и так лежит.
#
# Живёт здесь, а не в маршруте генерации, потому что нужен обоим: кадру — чтобы
# подставить лицо, разговору — чтобы сказать, что лица нет.
_SELF_WORDS = re.compile(
    r"\b(me|myself|us|my (photo|face|picture))\b|меня|себя|нас\b|вдво[её]м"
    r"|\bя\b|\bмне\b|\bмо[йяё]\b|мо[её] (фото|лицо)",
    re.IGNORECASE,
)


def asks_for_self(text: str | None) -> bool:
    """Просит ли человек себя в кадре — его же словами."""
    return bool(text and _SELF_WORDS.search(text))


def said_in(text: str, *, ru: str, en: str) -> str:
    """Готовая реплика на языке человека.

    Написанное моделью само приходит на его языке — так ей и сказано. Но
    несколько строк набраны руками: приветствие без ключа к модели, извинение за
    отказ сети, вопрос «кто из них вы». Оставить их английскими значило бы
    отвечать на двух языках в одной переписке.

    Различаем по письму, а не по словам: кириллица — русский, всё остальное —
    английский. Для двух языков этого достаточно, а угадывать третий по строке
    «ок» всё равно нечем.
    """
    return ru if any("а" <= ch.lower() <= "я" or ch.lower() == "ё" for ch in text) else en


def chat_directive(ask_about: str | None, known: dict[str, str] | None = None,
                   *, photo_attached: bool = False, no_face_on_file: bool = False) -> str:
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
    if no_face_on_file:
        # Человек просит себя, а лица у нас нет ни приложенного, ни в профиле.
        # Молча нарисовать постороннего и списать за это TOONTOON — худший из
        # возможных ответов: узнать об этом можно только на готовом кадре.
        lines.append(
            "The person asked for THEMSELVES in the picture, but no photo of them is "
            "attached and none is on file. Say this plainly in one short sentence: "
            "without a photo the face will not be theirs, and attaching one makes it "
            "them. Do not refuse and do not lecture — they may go ahead either way."
        )
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
    no_face_on_file: bool = False,
) -> str:
    """Return Toontoon's reply to a user message in the chat.

    ``history`` is a list of ``{"role": "user"|"assistant", "content": str}``.
    Falls back to a static reply if OpenAI is not configured.

    ``ask_about`` и ``known`` — что спросить и что уже известно. Без них модель
    выбирает вопрос сама, и выбирает плохо: свободный чат так ни разу не спросил
    про пропорции и дважды вернулся к стилю, названному первой же фразой.
    """
    if not settings.openai_enabled:
        return said_in(message,
                       ru="Привет! Я Toontoon. Что сделаем — картинку или открытку?",
                       en="Hi! I'm Toontoon. What would you like to create — "
                          "a picture or a postcard?")

    # Указание стоит после истории, а не до неё: модель тем сильнее слушает, чем
    # ближе к концу написано. Стоя первым, оно проигрывало разговору — человек
    # уже ответил про формат, а она спрашивала о нём снова, потому что двадцатью
    # репликами выше этот вопрос звучал.
    messages = [{"role": "system", "content": _CHAT_SYSTEM}]
    messages.extend(history[-10:])  # Keep last 10 turns for context.
    messages.append({"role": "system", "content": chat_directive(
        ask_about, known, photo_attached=photo_attached,
        no_face_on_file=no_face_on_file)})
    messages.append({"role": "user", "content": message})

    try:
        return await _call(messages, max_tokens=200)
    except Exception:
        return said_in(message,
                       ru="Извините, сейчас не получается ответить. Попробуйте через минуту.",
                       en="Sorry, I'm having trouble right now. Please try again in a moment.")


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
    # «Постер про баскетбол» разбирался как `place: basketball` — и новогодняя
    # открытка потом уезжала с баскетбольным местом. Про что картинка и где она
    # происходит — разные вопросы, и второй бывает без ответа чаще, чем кажется.
    "place": (
        "the physical location the scene happens in — a room, a street, a "
        "rooftop, a beach — and only when they say where. What the picture is "
        "ABOUT is not a location: «a poster about basketball» says what it is "
        "about, not where it happens, and place stays empty there"
    ),
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
    "You are shown the prompt that produced a picture, and you answer the "
    "person who just got it: a short warm line about the picture that ends by "
    "opening the door to more, then what could be changed next. Your reader is "
    "not a designer: the hardest part for them is not tapping, it is knowing "
    "what to ask for.\n"
    "Format, exactly five lines and nothing else:\n"
    "Line 1 — one sentence, twelve words at most, ending in a question that "
    "invites another go: «That turned out sharp. Want to push it further?». "
    "Name what is actually in this picture — a line that fits any result is "
    "not worth reading.\n"
    "Lines 2 to 5 — four ideas.\n"
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


async def next_step_ideas(*, prompt: str, intent: str | None = None,
                          spoken: str = "") -> tuple[str, list[str]]:
    """Слово о готовом кадре и четыре правки к нему.

    Слово — с открытым вопросом: разговор не должен заканчиваться картинкой.
    Человек получил кадр и остаётся один на один с пустым полем ввода; вопрос
    «хотите что-нибудь с ней сделать?» стоит дешевле любой кнопки и работает
    лучше — на него отвечают.

    Пустой ответ законен: модель отказала или промпта нет. Приложение тогда
    показывает обычные кнопки уточнений, и человек не видит поломки.

    ``spoken`` — чем человек писал в этом разговоре. Слово о кадре ложится в
    переписку строкой ассистента, а происходит это после каждой генерации:
    английская реплика посреди русского разговора здесь заметнее всего. Сам
    промпт языка не подсказывает — он всегда английский, это машинерия.
    """
    if not settings.openai_enabled or not prompt.strip():
        return "", []

    what = f"This is a {intent}." if intent else ""
    try:
        raw = await _call(
            [
                {"role": "system", "content": _IDEAS_SYSTEM + said_in(
                    spoken,
                    ru="\nWrite the remark and all four ideas in Russian, "
                       "addressing the person as «вы».\n",
                    en="")},
                {"role": "user", "content": f"{what}\nThe picture was made with:\n{prompt}"},
            ],
            max_tokens=260,
            temperature=0.7,
            model=settings.slot_extraction_model or None,
        )
    except Exception:  # noqa: BLE001 — без идей экран живёт, без кадра нет
        return "", []

    lines = _clean_idea_lines(raw, limit=5)
    if not lines:
        return "", []
    return lines[0], lines[1:]


def _clean_idea_lines(raw: str, *, limit: int = 4) -> list[str]:
    """Строки предложений без нумерации и маркеров.

    Модель то нумерует, то ставит маркеры, сколько ни проси. Снимаем это кодом,
    а не очередной строкой в промпте: правило, которое можно выполнить кодом, в
    промпте только занимает внимание.
    """
    ideas = []
    for line in raw.splitlines():
        cleaned = line.strip().lstrip("-•*0123456789.） )").strip().strip('"').strip()
        if len(cleaned) > 3:
            ideas.append(cleaned)
    return ideas[:limit]


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


async def reference_roles(
    images: list[tuple[bytes, str]],
    message: str,
    *,
    person_known: bool = False,
) -> list[str]:
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
    # Одну картинку разбираем только тогда, когда человек нам уже известен: без
    # профиля единственный приложенный снимок — это он сам, и спрашивать не о
    # чем. С профилем всё наоборот: лицо у нас есть, и приложенное чаще всего
    # образец — «сделай меня в стилистике вот этого».
    least = 1 if person_known else 2
    if not settings.openai_enabled or len(images) < least:
        return []

    known = ("We already know what this person looks like, so a picture they "
             "attach is often a sample rather than themselves.\n" if person_known else "")
    parts: list[dict] = [{
        "type": "text",
        "text": f"The person wrote: {message.strip() or '(nothing)'}\n"
                f"{known}"
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
    if len(roles) != len(images):
        return []
    # Без профиля кто-то обязан быть субъектом: ответ «всё образцы» означал бы
    # кадр без единого человека, чего никто не просил. С профилем это законно —
    # субъект уже есть, он просто не на приложенных картинках.
    if "person" not in roles and not person_known:
        return []
    return roles

_SAMPLE_STYLE_SYSTEM = (
    "You are shown one picture a person attached as a STYLE SAMPLE — they want "
    "their own picture made to look like it. Name the closest technique from "
    "this list and answer with that word alone, nothing else:\n"
    "- realistic — a photograph\n"
    "- scene_epic — cinematic, dramatic, epic scale\n"
    "- scene_cozy — cosy, warm, homely illustration\n"
    "- 3d_cartoon — 3D cartoon, animated feature film look\n"
    "- semi_real_3d — stylised 3D with lifelike proportions\n"
    "- anime — anime, manga, cel-shaded illustration\n"
    "Judge how it is DRAWN, not what it shows: a drawing of a real place is "
    "still a drawing.\n"
    "Then read every word, name and piece of lettering anywhere in the image — "
    "headlines, jerseys, crests, badges, seals, small print, signatures — in "
    "any script. Of those, report ONLY the ones that name something real: a "
    "company, a brand, a team, a league, a publication, a person's name. "
    "Invented words, common words and decorative lettering are not brands and "
    "must be left out.\n"
    'Answer JSON and nothing else: {"style": "<one id above>", '
    '"brands": ["<each real name you can read, exactly as written>"]}'
)


async def style_of_sample(image: tuple[bytes, str]) -> tuple[str | None, list[str]]:
    """Какой техникой сделан образец и чьи марки на нём написаны.

    Образец показан картинкой, и прочитать его можно только глазами. Ждать,
    что человек назовёт технику словами, бессмысленно: он затем и приложил
    картинку, чтобы не описывать её, — «сделай в такой же стилистике» это
    полный ответ, просто данный не текстом.

    Без этого техника оставалась неназванной, а неназванная означала
    фотографию: в промпт уходил фотореалистичный якорь и следом просьба
    скопировать рисунок с образца. Якорь стоит первым и выигрывает — человек
    прикладывал аниме-постер и получал свою фотографию.

    Марки — вторым делом и тем же вызовом, потому что второй стоил бы столько
    же, сколько первый. Запрет «не переноси чужой бренд» описывает категорию, и
    на категорию модель отзывается через раз: замер на пяти кадрах дал два, где
    «AKAI» уцелело в шевроне размером в сто пикселей. Названное слово — не
    категория, и запретить его можно буквально.

    Спрашиваем именно марки, а не все надписи. Риск здесь не в буквах, а в
    чужом знаке: «AKAI» принадлежит настоящей компании, а «赤い伝説» — «Красная
    легенда» — не принадлежит никому, и мешать ему остаться в кадре незачем.
    Заодно это решает, когда переснимать: пустой список марок означает, что
    проверять готовый кадр не нужно вовсе, а таких образцов большинство.
    """
    if not settings.openai_enabled:
        return None, []

    data, _ = image
    small = base64.b64encode(storage_images.preview(data)).decode()
    try:
        raw = await _call(
            [{"role": "system", "content": _SAMPLE_STYLE_SYSTEM},
             {"role": "user", "content": [
                 {"type": "image_url",
                  "image_url": {"url": f"data:image/jpeg;base64,{small}"}}]}],
            max_tokens=200,
            temperature=0,
            model=settings.slot_extraction_model or None,
        )
    except Exception:  # noqa: BLE001 — не разобрали, значит не называем
        return None, []

    try:
        start, end = raw.index("{"), raw.rindex("}") + 1
        parsed = json.loads(raw[start:end])
    except (ValueError, AttributeError, json.JSONDecodeError):
        return None, []

    key = str(parsed.get("style") or "").strip().strip(".").lower().split()
    style = key[0] if key and key[0] in prompt_style.PRESETS else None
    words, seen = [], set()
    for raw_word in (str(w).strip() for w in parsed.get("brands") or []):
        # Однобуквенное и числа запрещать бессмысленно: номер на майке это не
        # бренд, а запрет на «1» испортил бы любую вёрстку.
        if len(raw_word) < 2 or raw_word.isdigit():
            continue
        # Одно и то же слово зрение перечисляет столько раз, сколько видит его
        # на картинке: «AKAI» пришло пятью строками. В запрете это лишний вес.
        if (key := raw_word.casefold()) in seen:
            continue
        seen.add(key)
        words.append(raw_word)
    return style, words[:12]


_BRANDS_SEEN_SYSTEM = (
    "You are shown one picture and a list of names. Answer with the names from "
    "the list that actually appear written in the picture — anywhere, at any "
    "size, including tiny badges, crests, seals and small print. Match by what "
    "the letters say, not by exact spelling: a name written in another script, "
    "or with one letter different, still counts.\n"
    'Answer JSON and nothing else: {"seen": ["<names from the list>"]}'
)


async def brands_on_image(data: bytes, names: list[str]) -> list[str]:
    """Какие из этих марок написаны в кадре.

    Спрашиваем моделью, а не чтением: наш OCR — системное зрение macOS, в проде
    его нет. И сверка списком надёжнее точного совпадения строк: зрение читает
    «KOVRA» как «KOYRA», а модель, которой дали список, узнаёт слово всё равно.

    Пустой список имён — пустой ответ и ни одного вызова: у большинства
    образцов чужих марок нет вовсе, и платить за проверку там не за что.
    """
    if not names or not settings.openai_enabled or not data:
        return []

    small = base64.b64encode(storage_images.preview(data, side=1024)).decode()
    try:
        raw = await _call(
            [{"role": "system", "content": _BRANDS_SEEN_SYSTEM},
             {"role": "user", "content": [
                 {"type": "text", "text": "Names: " + ", ".join(names)},
                 {"type": "image_url",
                  "image_url": {"url": f"data:image/jpeg;base64,{small}"}}]}],
            max_tokens=120,
            temperature=0,
            model=settings.slot_extraction_model or None,
        )
    except Exception:  # noqa: BLE001 — не прочитали, значит не переснимаем
        return []
    try:
        start, end = raw.index("{"), raw.rindex("}") + 1
        seen = json.loads(raw[start:end]).get("seen") or []
    except (ValueError, AttributeError, json.JSONDecodeError):
        return []
    wanted = {n.casefold() for n in names}
    return [str(x).strip() for x in seen if str(x).strip().casefold() in wanted]


_IS_DRAWING_SYSTEM = (
    "You are shown one finished picture. Answer with one word and nothing "
    "else: PHOTOGRAPH if it is a photograph (or a photographic face pasted "
    "onto a drawn background), DRAWING if it is drawn or rendered — anime, "
    "comic, 3D cartoon, painting. Judge how it is made, not what it shows."
)


async def looks_photographic(data: bytes) -> bool:
    """Вернулась ли фотография там, где просили рисунок.

    Проверка нужна, потому что редактор срывается молча: промпт верный, стиль
    назван трижды, а на выходе снимок человека на рисованном фоне. Для него это
    не ошибка, для человека — брак, за который он заплатил.

    Сомнение трактуем в пользу картинки: «не разобрали» значит «отдаём как
    есть». Лишний повтор стоит денег, а лишняя задержка — доверия.
    """
    if not settings.openai_enabled:
        return False

    small = base64.b64encode(storage_images.preview(data)).decode()
    try:
        raw = await _call(
            [{"role": "system", "content": _IS_DRAWING_SYSTEM},
             {"role": "user", "content": [
                 {"type": "image_url",
                  "image_url": {"url": f"data:image/jpeg;base64,{small}"}}]}],
            max_tokens=8,
            temperature=0,
            model=settings.slot_extraction_model or None,
        )
    except Exception:  # noqa: BLE001
        return False
    return "photograph" in (raw or "").strip().lower()


# ─── Что можно сделать с приложенным снимком ─────────────────────────────────

_PHOTO_IDEAS_SYSTEM = (
    "You are shown a photograph a person just attached, and you answer it: "
    "first a remark about the picture itself, then what you could make out of "
    "it. They have not said a word yet — the picture is the whole request so "
    "far, and your lines are the first thing they read.\n"
    "Format, exactly five lines and nothing else:\n"
    "Line 1 — one warm human sentence about THIS picture, twelve words at most. "
    "Notice something real: the light, the pose, the calm, the colour of the "
    "wall. «Great portrait» fits any photograph and reads as flattery; «lovely "
    "soft window light on that one» reads as looking.\n"
    "Lines 2 to 5 — four ideas.\n"
    "Rules:\n"
    "- Exactly four ideas, one per line. No numbering, no bullets, no quotes.\n"
    "- Each line is a finished instruction they can send as it is: «Turn this "
    "into an anime poster in white and blue», «Put me on a rooftop at sunset "
    "in cinematic light». Six to fourteen words.\n"
    "- Address the person as «me» — it is their own photo.\n"
    "- Look at what is actually in the picture and use it: what they wear, "
    "where they are, the light, the mood. A stranger's idea would fit any "
    "photograph; yours must fit this one.\n"
    "- Four different directions, not four shades of one. Vary the technique "
    "(a photograph, anime, a 3D cartoon), the setting and the purpose "
    "(a portrait, a poster, a greeting card).\n"
    "- If there is no person in the picture, propose what to do with the thing "
    "or the place that is there, and never invent a person.\n"
    "- English, plain and warm. Never name a studio, a brand or a league.\n"
)


async def photo_remark_and_ideas(image: bytes, *, spoken: str = "") -> tuple[str, list[str]]:
    """Что сказать про снимок и что предложить с ним сделать.

    Снимок — это уже просьба, просто без слов: человек приложил своё лицо и
    ждёт, что мы предложим. Пустой экран с мигающей строкой ввода на этом месте
    возвращает его к чистому листу, а лист — самая дорогая часть работы.

    Реплика идёт первой строкой, потому что разговор начинается с ответа на
    показанное, а не со списка услуг. «Хороший портрет» подходит к любой
    фотографии и читается как лесть; сказать про свет из окна — значит
    посмотреть.

    Пустой ответ законен: зрение недоступно или отказало. Тогда человек просто
    пишет сам, как писал бы всегда.

    ``spoken`` — чем человек писал до этого. Снимок приходит без слов, и языка в
    самой реплике нет; взять его больше неоткуда, а английская строка посреди
    русской переписки — та же чужая реплика, что и раньше.
    """
    if not settings.openai_enabled or not image:
        return "", []

    small = base64.b64encode(storage_images.preview(image, side=512)).decode()
    try:
        raw = await _call(
            [
                {"role": "system", "content": _PHOTO_IDEAS_SYSTEM + said_in(
                    spoken,
                    ru="\nWrite the remark and the ideas in Russian, addressing "
                       "the person as «вы».\n",
                    en="")},
                {"role": "user", "content": [
                    {"type": "text", "text": "Here is the photograph."},
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/jpeg;base64,{small}"}},
                ]},
            ],
            max_tokens=260,
            temperature=0.8,
            model=settings.slot_extraction_model or None,
        )
    except Exception:  # noqa: BLE001 — без идей экран живёт
        return "", []

    lines = _clean_idea_lines(raw, limit=5)
    if not lines:
        return "", []
    # Первая строка — реплика, остальные предложения. Если модель ответила
    # одними идеями, реплики просто не будет: выдумывать её из первой идеи
    # значило бы предложить сделать то, что мы уже якобы разглядели.
    return lines[0], lines[1:]

_STARTER_IDEAS_SYSTEM = (
    "A person has opened an app that makes pictures of them, and has not "
    "attached anything or said anything yet. Propose four things worth making.\n"
    "Rules:\n"
    "- Exactly four lines, one idea each. No numbering, no bullets, no quotes.\n"
    "- Each line is a finished request they could send as it is, six to "
    "fourteen words, written as «me»: «Make an anime poster of me in white and "
    "blue».\n"
    "- Four different directions: a portrait, a poster, a card to send, "
    "something playful. Vary the technique and the mood.\n"
    "- Concrete and visual. «Something creative» is not an idea.\n"
    "- English, plain and warm. Never name a studio, a brand or a league.\n"
)


async def starter_ideas() -> list[str]:
    """С чего начать, когда нет ни снимка, ни кадра, ни слова.

    Пустой экран и мигающая строка ввода — это чистый лист, а лист и есть самая
    дорогая часть работы. Четыре готовые фразы стоят сотые доли цента и
    отвечают на единственный вопрос, который у человека есть в эту секунду:
    «а что тут вообще можно?»
    """
    if not settings.openai_enabled:
        return []
    try:
        raw = await _call(
            [
                {"role": "system", "content": _STARTER_IDEAS_SYSTEM},
                {"role": "user", "content": "What should I make?"},
            ],
            max_tokens=200,
            temperature=0.9,
            model=settings.slot_extraction_model or None,
        )
    except Exception:  # noqa: BLE001 — без идей экран живёт
        return []
    return _clean_idea_lines(raw)

# ─── Разбор набора снимков для профиля ───────────────────────────────────────

# Сколько снимков имеет смысл отобрать в опорный набор.
#
# Шесть — не предел вендора (там четырнадцать-шестнадцать), а предел смысла:
# дальше идут повторы того, что уже покрыто, а каждый лишний референс это ещё
# один вход в запросе и ещё один шанс, что модель усреднит черты.
MAX_REFERENCE_PHOTOS = 6

_PROFILE_REVIEW_SYSTEM = (
    "You are shown the photographs a person picked for their profile — the set "
    "we will use to put them into every picture they ask for. Judge the set "
    "the way a photographer would before a shoot: what is usable, what is not, "
    "and what is missing.\n"
    "What a good photo here is:\n"
    "- exactly one person in the frame;\n"
    "- the face turned enough to be read, not lost in shadow, not covered by "
    "sunglasses, a hand or a mask;\n"
    "- the face large enough to see the features, and in focus;\n"
    "- a photograph of a real person — not a drawing, not a screenshot, not a "
    "poster.\n"
    "What a good SET has, beyond good photos: a face-on shot and a "
    "three-quarter one, a smile and a calm face, more than one kind of light, "
    "and not the same clothes and wall in every frame — otherwise the sweater "
    "and the wall get learned as part of the person.\n"
    "Answer with JSON only:\n"
    '{"photos": [{"index": 1, "ok": true, "reason": ""}, ...], '
    '"missing": ["...", "..."], "chosen": [3, 1, 7]}\n'
    "Rules:\n"
    "- One entry per photograph, in the order given, numbering from 1.\n"
    "- `reason` only when `ok` is false: one short phrase saying what is wrong, "
    "addressed to the person — «two people in the frame», «face is too small», "
    "«sunglasses hide the eyes». Empty when the photo is fine.\n"
    "- `missing` — at most three lines, each naming one photograph worth "
    "adding: «one where you are smiling», «one in daylight». Empty when the set "
    "is good enough.\n"
    "- `chosen` — the photographs we should actually work from, best first, at "
    "most six. Pick the SMALLEST set that covers the person: one face-on, one "
    "three-quarter, one with a different expression, one in different light, "
    "one further away for the build. Prefer sharp photos with a large readable "
    "face. Never pick two that show the same thing — a second copy of a shot we "
    "already have adds nothing and crowds out what is missing. Skip anything "
    "you marked as not ok.\n"
    "- Watch what repeats across the chosen ones. A hat, glasses, headphones or "
    "the same jacket in every chosen photo get learned as part of the person, "
    "and they will show up in pictures that never asked for them. Prefer an "
    "uncovered head and a bare face where the set allows it, and never let the "
    "same accessory appear in every chosen photo when a photo without it "
    "exists.\n"
    "- Be strict about faces and generous about everything else: a plain photo "
    "with a readable face is fine even if it is dull.\n"
)


async def review_profile_photos(images: list[bytes]) -> dict:
    """Что из набора годится и какого снимка не хватает.

    Разбор до сборки профиля, а не после: набор решает всё, что будет дальше.
    Двадцать кадров в одном свитере у одной стены дают профиль, который считает
    свитер и стену частью человека, и заметно это станет на десятой генерации,
    когда менять будет поздно.

    Пустой ответ законен: зрение недоступно. Тогда профиль собирается как есть —
    отказывать человеку в профиле из-за того, что мы не смогли посмотреть,
    было бы наказанием за нашу же неисправность.
    """
    if not settings.openai_enabled or not images:
        return {"photos": [], "missing": [], "chosen": []}

    parts: list[dict] = [{"type": "text",
                          "text": f"{len(images)} photographs, in order."}]
    for data in images:
        small = base64.b64encode(storage_images.preview(data, side=512)).decode()
        parts.append({"type": "image_url",
                      "image_url": {"url": f"data:image/jpeg;base64,{small}"}})

    try:
        raw = await _call(
            [{"role": "system", "content": _PROFILE_REVIEW_SYSTEM},
             {"role": "user", "content": parts}],
            max_tokens=600,
            temperature=0,
            model=settings.slot_extraction_model or None,
        )
        start, end = raw.index("{"), raw.rindex("}") + 1
        parsed = json.loads(raw[start:end])
    except Exception:  # noqa: BLE001 — не посмотрели, значит не мешаем
        return {"photos": [], "missing": [], "chosen": []}

    photos = []
    for item in parsed.get("photos", [])[:len(images)]:
        photos.append({
            "index": int(item.get("index") or len(photos) + 1),
            "ok": bool(item.get("ok")),
            "reason": str(item.get("reason") or "").strip(),
        })
    missing = [str(m).strip() for m in parsed.get("missing", [])[:3] if str(m).strip()]

    # Отобранные — по порядку полезности и без повторов. Номера приходят от
    # модели, поэтому проверяем каждый: чужой индекс означал бы, что мы
    # подставим человеку не его фотографию.
    chosen: list[int] = []
    for value in parsed.get("chosen", [])[:MAX_REFERENCE_PHOTOS]:
        try:
            index = int(value)
        except (TypeError, ValueError):
            continue
        if 1 <= index <= len(images) and index not in chosen:
            chosen.append(index)
    return {"photos": photos, "missing": missing, "chosen": chosen}
