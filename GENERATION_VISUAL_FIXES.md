# ARTEKI — Фиксы генерации: визуально-эстетический слой

> База: `GENERATION_SPEC.md` + аудит `arteki/docs/prompt-templates-*.md`.
> Предыдущий файл `GENERATION_FIXES.md` закрыл **семантические признаки субъекта**
> (живое/неживое, род, число, возраст). Этот файл закрывает второй слой:
> **как выглядит картинка** — поза, мимика, палитра, свет, композиция, обаяние.
>
> ⚠️ Код `/opt/backend` мне недоступен — имена символов взяты из `GENERATION_SPEC.md`,
> сниппеты ниже **проектные (drop-in proposal)**, перед коммитом сверить с реальным кодом.

---

## TL;DR — корневая проблема

Промпт описывает **ЧТО** в кадре (субъект + сцена + декор), но почти не описывает **КАК
это выглядит**: выражение лица, поза, силуэт, обаяние, конкретная палитра, тип света,
кадрирование. Это отдаётся модели → персонажи статичные, цвет случайный, субъект мелкий.

### Три главные дыры

| Дыра | Следствие |
|---|---|
| **Нет слоя «appeal» персонажа** — единственный маркер `big expressive friendly eyes` | персонажи «мёртвые»: ноль позы, мимики, взгляда, силуэта |
| **Палитра не фиксируется** — `bold warm saturated colors` слишком общо | разнобой цветов и «грязь» от раза к разу |
| **Композиция/фокус не управляются** — нет hero-framing, глубины | субъект мелкий или теряется на фоне |

---

## Фиксы: сводная таблица

| # | Фикс | Файл | Приоритет |
|---|---|---|---|
| VIS-1 | Добавить слой appeal в `STYLE_3D` | `prompt_style.py` | 🔴 P0 |
| VIS-2 | Именованные палитры по типу открытки/сцены | `card_prompts.py`, `picture_prompts.py` | 🔴 P0 |
| VIS-3 | Слой `{EXPRESSION}` + `{POSE}` + взгляд в камеру | `card_prompts.py`, `picture_prompts.py` | 🔴 P0 |
| VIS-4 | Слой `{COMPOSITION}` — hero-framing + глубина | все prompts | 🟡 P1 |
| VIS-5 | Слой `{LIGHT}` — именованные типы света | все prompts | 🟡 P1 |
| VIS-6 | Дополнить `NEGATIVE` визуальными блокировками | `prompt_style.py` | 🟡 P1 |
| VIS-7 | Обогащение free-text: скелет обязательных слоёв | `gpt.py` / free-text инструкция | 🟡 P1 |
| VIS-8 | OpenAI Images: визуальные guards в позитив | `picture_prompts.py`, `card_prompts.py` | 🟢 P2 |
| VIS-9 | Brand-guard regex | `prompt_style.py` | 🟢 P2 |

---

## VIS-1 🔴 Обновить `STYLE_3D` — добавить слой appeal

**Проблема.** Текущий `STYLE_3D`:
```
vibrant 3D cartoon render, modern animated feature film look, big expressive friendly eyes,
soft rounded chunky shapes, smooth glossy surfaces with subtle texture,
bold warm saturated colors, cheerful charming character design, bright modern animation quality
```
`big expressive friendly eyes` — единственный маркер выразительности. Нет позы, мимики,
силуэта, обаяния → персонажи «мёртвые болванчики».

**Фикс.** Добавить блок appeal после основного стиля:

```python
STYLE_3D = (
    "vibrant 3D cartoon render, modern animated feature film look, "
    "big expressive eyes with lively catchlights, "
    "soft rounded chunky shapes, smooth glossy surfaces with subtle texture, "
    "bold warm saturated colors, "
    # NEW: appeal layer
    "premium stylized character design with strong character appeal, "
    "clean readable silhouette, soft rounded appealing shapes, "
    "charismatic likeable personality, "
    "bright toy-like materials with rich micro-texture"
)
```

> ⚠️ Никогда не писать: `Pixar`, `Disney`, `DreamWorks`, `Illumination`, `Ghibli`,
> `in the style of`. Используем функциональные дескрипторы — см. VIS-9.

---

## VIS-2 🔴 Именованные палитры

**Проблема.** `bold warm saturated colors` → модель каждый раз выбирает цвет сама.
Главный источник непредсказуемости и «грязи».

**Фикс.** Словарь именованных палитр в `prompt_style.py`:

```python
PALETTES = {
    # Праздники
    "birthday":     "harmonious palette of warm coral, soft cream and gold accents, vivid but not acidic",
    "jubilee":      "rich gold, champagne cream and warm ivory, prestigious warm tones",
    "graduation":   "rich purple, royal gold and warm white, triumphant and bright",
    "new_year":     "deep midnight blue, sparkling silver, warm gold and soft white glow",

    # Нежность / любовь
    "valentine":    "soft rose pink, deep red and warm champagne gold, tender romantic palette",
    "anniversary":  "deep crimson, warm gold and soft cream, intimate and romantic",
    "mothers_day":  "soft blush pink, rose and champagne cream, delicate and tender",
    "wedding":      "pure white, soft ivory and warm gold with silver accents",

    # Природа / сезоны
    "spring":       "fresh mint, peach blossom and butter yellow, clean and airy",
    "easter":       "pastel yellow, soft mint and light peach, gentle spring tones",
    "thanksgiving": "rich amber, burnt orange, cream and warm brown harvest tones",
    "autumn":       "deep amber, rust red and golden-brown, warm saturated autumn palette",

    # Характер / динамика
    "fathers_day":  "teal, warm amber and natural green with golden sunlight accents",
    "get_well":     "bright sunny yellow, soft green and warm sky blue, uplifting and fresh",
    "good_morning": "warm peach, golden yellow and soft cream, gentle sunrise tones",

    # Живые персонажи (дефолт)
    "character":    "bright vivid warm palette, clean bold colors with harmonious contrast",

    # Сцены (дефолт)
    "scene_cozy":   "warm naturalistic saturated colors, rich earthy and botanical tones",
    "scene_epic":   "bold dramatic deep tones, vivid contrast with majestic atmosphere",
}

def get_palette(tile_id: str, is_living: bool) -> str:
    if tile_id in PALETTES:
        return PALETTES[tile_id]
    return PALETTES["character"] if is_living else PALETTES["scene_cozy"]
```

**Использование в шаблоне:**
```python
# вставляем после сцены, перед QUALITY_BLOCK
prompt_parts.append(get_palette(tile_id, is_living))
```

---

## VIS-3 🔴 Слои expression, pose, взгляд

**Проблема.** Шаблоны `{WHO_CHAR}` типа `adorable cheerful adult character` полностью
игнорируют мимику, позу и взгляд. Персонаж «стоит в кадре», не «живёт».

**Фикс.** Словарь слоёв по типу открытки. Добавить в `card_prompts.py`:

```python
# Слой expression + gaze (что на лице + смотрит ли в камеру)
EXPRESSION = {
    "birthday":     "warm genuine beaming smile, sparkling eyes with lively catchlights looking at the viewer",
    "jubilee":      "dignified proud warm expression, kind eyes with a gentle knowing smile, looking at the viewer",
    "valentine":    "tender loving gaze, soft warm smile, eyes filled with warmth and affection, looking at viewer",
    "anniversary":  "tender loving gaze, gentle warm smile, full of warmth and affection",
    "mothers_day":  "gentle tender expression, soft loving eyes, warm nurturing presence, looking at viewer",
    "fathers_day":  "confident proud warm smile, strong gentle eyes, looking at the viewer",
    "wedding":      "radiant joyful expression, eyes sparkling with happiness, beaming warm smile",
    "graduation":   "proud triumphant smile, bright excited eyes with a sense of achievement",
    "easter":       "cheerful joyful smile, bright delighted eyes, playful expression",
    "new_year":     "excited magical expression, wide sparkling eyes full of wonder and joy",
    "get_well":     "warm hopeful gentle smile, kind caring eyes, uplifting and comforting presence",
    "thanksgiving": "warm grateful expression, content happy smile, cozy and welcoming",
    "good_morning": "peaceful serene gentle smile, soft warm eyes, calm awakening expression",
    "good_day":     "bright cheerful smile, energetic happy eyes, positive and inviting",
    "just_because": "playful warm smile, delighted surprised eyes, spontaneous joyful energy",
}

# Слой pose / body language (поза, что делает тело)
POSE = {
    "birthday":     "arms raised mid-celebration in a joyful dynamic pose, mid-gesture",
    "jubilee":      "dignified upright pose with a gracious welcoming gesture",
    "valentine":    "gentle tender pose, hands near heart or holding flowers, soft and romantic",
    "mothers_day":  "graceful gentle pose, arms outstretched or holding bouquet, nurturing presence",
    "fathers_day":  "confident relaxed upright pose, one hand raised in a warm greeting",
    "graduation":   "triumphant pose with arms raised, graduation cap thrown in the air",
    "easter":       "playful leaning pose, hands holding Easter basket or decorated egg",
    "new_year":     "excited dynamic pose, arms raised with sparkles, mid-celebration",
    "get_well":     "gentle comforting soft pose, offering warmth and care",
    "good_morning": "cozy relaxed sitting pose, holding a warm cup, peaceful and content",
    "good_day":     "upbeat confident walking or waving pose, full of energy",
    "just_because": "spontaneous joyful pose, mid-laugh or mid-wave, light and playful",
    # дефолт для остальных
    "_default":     "natural lively body language, mid-gesture, dynamic and expressive pose",
}

def get_expression(tile_id: str) -> str:
    return EXPRESSION.get(tile_id, "warm genuine smile, lively expressive eyes looking at the viewer")

def get_pose(tile_id: str) -> str:
    return POSE.get(tile_id, POSE["_default"])
```

**Вставка в `assemble_prompt()`** — после `{subject}`, до `{action}`:
```python
layers = [
    style_anchor,
    subject_block,          # из паспорта (FIX-2 из GENERATION_FIXES.md)
    get_expression(tile_id),# NEW: мимика + взгляд
    get_pose(tile_id),      # NEW: поза
    APPEAL_BLOCK,           # NEW: силуэт + обаяние (константа ниже)
    action,
    scene,
    ...
]

APPEAL_BLOCK = (
    "clean readable silhouette, strong character appeal, "
    "charismatic and likeable, soft rounded appealing shapes"
)
```

---

## VIS-4 🟡 Слой composition — hero-framing + глубина

**Проблема.** Текущие шаблоны используют `subject centered in frame, soft bokeh background,
clean uncluttered composition` — слишком общо. Субъект часто мелкий, фокус размыт.

**Фикс.** Именованные режимы кадрирования в `prompt_style.py`:

```python
COMPOSITION = {
    # Открытки с персонажем (адресат = герой кадра)
    "card_hero":
        "hero framing, subject large in frame, eye-level, clear single focal point, "
        "soft background bokeh, layered depth with subject sharp and separated from background, "
        "balanced negative space in upper third for text overlay",

    # Персонаж-картинка (cartoon-char, cute-animal)
    "character":
        "hero medium shot, subject centered and large in frame, eye-level, "
        "clear single focal point, soft background bokeh with gentle depth",

    # Пейзаж уютный (cozy)
    "scene_cozy":
        "intimate close-in framing, tidy composition, single clear focal object, "
        "rich but uncluttered layered depth, soft background bokeh",

    # Пейзаж эпичный (epic)
    "scene_epic":
        "wide establishing shot, dramatic horizon line, rule of thirds, "
        "bold foreground-midground-background depth layers, majestic scale",

    # Еда (food hero shot)
    "food":
        "appetizing overhead or 3/4 close-up, subject fills the frame, "
        "mouth-watering hero framing, clean surface, soft side lighting",
}

def get_composition(tile_id: str, style_key: str) -> str:
    if tile_id in ("food",):
        return COMPOSITION["food"]
    if style_key == "scene_epic":
        return COMPOSITION["scene_epic"]
    if style_key == "scene_cozy":
        return COMPOSITION["scene_cozy"]
    if style_key == "3d_cartoon" and "card" in tile_id:
        return COMPOSITION["card_hero"]
    return COMPOSITION["character"]
```

---

## VIS-5 🟡 Слой light — именованные типы света

**Проблема.** `warm cinematic lighting with soft rim light` — один тип на всё. Ночная
магия новогодней открытки не отличается от утреннего пейзажа.

**Фикс.** Типы света в `prompt_style.py`:

```python
LIGHT = {
    "warm_studio":
        "soft three-point studio lighting, warm key light with gentle rim light "
        "and soft bounce fill, no harsh shadows",

    "golden_hour":
        "warm golden hour sunlight, long soft shadows, rich amber glow, "
        "gentle lens flare and warm atmospheric haze",

    "morning_soft":
        "soft diffuse morning light, clean cool-to-warm gradient, "
        "gentle sunrise rays through window, peaceful and bright",

    "magical_glow":
        "magical dreamy inner glow, soft sparkle light, ethereal light rays, "
        "luminous warm atmosphere with bokeh light particles",

    "candlelight":
        "warm intimate candlelight, soft orange-gold glow, gentle flickering warmth, "
        "deep warm shadows, cozy romantic atmosphere",

    "daylight_bright":
        "bright clean midday sunlight, vivid uplifting light, sharp natural shadows, "
        "fresh and energetic atmosphere",
}

LIGHT_BY_TILE = {
    "birthday":     "warm_studio",
    "jubilee":      "candlelight",
    "valentine":    "candlelight",
    "anniversary":  "candlelight",
    "mothers_day":  "morning_soft",
    "fathers_day":  "golden_hour",
    "wedding":      "magical_glow",
    "graduation":   "warm_studio",
    "easter":       "morning_soft",
    "new_year":     "magical_glow",
    "get_well":     "daylight_bright",
    "thanksgiving": "golden_hour",
    "good_morning": "morning_soft",
    "good_day":     "daylight_bright",
    "landscape":    "golden_hour",
    "food":         "warm_studio",
    # персонажные картинки
    "cartoon_char": "warm_studio",
    "cute_animal":  "warm_studio",
    "birds":        "morning_soft",
    "fish":         "magical_glow",
}

def get_light(tile_id: str) -> str:
    key = LIGHT_BY_TILE.get(tile_id, "warm_studio")
    return LIGHT[key]
```

---

## VIS-6 🟡 Дополнить `NEGATIVE`

**Проблема.** Текущий `NEGATIVE` блокирует уродство, но пропускает скучные/вялые картинки:
стоячую позу, «мёртвый» взгляд, плоскую мимику, грязную палитру, мелкий субъект.

**Фикс.** Расширить `NEGATIVE_PROMPT` в `prompt_style.py`:

```python
NEGATIVE_PROMPT = (
    # существующие блоки (не трогать)
    "distorted anatomy, extra fingers, extra limbs, fused fingers, cropped limbs, "
    "malformed hands, uncanny valley face, photorealistic human face, "
    "creepy expression, dead eyes, blank stare, "
    # NEW: визуально-эстетические блокировки
    "stiff lifeless pose, flat dull expression, boring symmetrical static stance, "
    "subject too small in frame, bad framing, empty wasted composition, "
    "muddy dirty colors, dull muted palette, washed out, oversaturated acidic neon, grey tones, "
    "messy cluttered background, busy distracting details, overcrowded composition, "
    "flat 2D illustration, flat vector art, matte plastic look, flat even lighting, "
    "text, letters, words, captions, watermark, signature, logo, "
    "low quality, jpeg artifacts, blurry, scary dark atmosphere"
)
```

**Для OpenAI Images** (негативный промпт игнорируется → кладём в позитив):

```python
OPENAI_VISUAL_GUARDS = (
    "clean correct anatomy with five fingers, friendly non-scary cartoon face, "
    "lively expressive eyes with bright catchlights, appealing dynamic natural pose, "
    "subject large and clear in frame, tidy uncluttered background, "
    "vivid clean harmonious colors, no muddy or washed out tones, "
    "NO text NO letters NO words NO watermark in the image, "
    "glossy textured surfaces (not flat, not matte plastic)"
)
```

---

## VIS-7 🟡 Скелет обогащения free-text

**Проблема.** Короткий ввод («котик с цветами») → LLM пишет сцену 30–60 слов без
обязательных визуальных слоёв. Поза/мимика/палитра/свет не заданы.

**Фикс.** Добавить в `build_free_text_instruction()` обязательный скелет:

```python
FREE_TEXT_VISUAL_SKELETON = """
When enriching the user's text into a full prompt, you MUST include ALL of the following layers
in this exact order:
1. STYLE ANCHOR — premium 3D animated movie look (or scene anchor if no living subject)
2. SUBJECT — from passport (animacy / gender / number / age)
3. EXPRESSION — specific emotion + eye direction (default: warm joyful, looking at viewer)
4. POSE — specific body language (default: natural lively mid-gesture pose)
5. APPEAL — clean silhouette, rounded shapes, strong character appeal
6. ACTION — what the subject is doing
7. SCENE — background environment + atmosphere
8. COMPOSITION — hero framing, focal point, depth (default: hero close-up, soft bokeh)
9. PALETTE — specific named colors fitting the mood (e.g. "warm coral, cream and gold")
10. LIGHT — specific light type (default: soft three-point studio lighting, warm rim light)
11. MATERIALS — toy-like surfaces, rich texture
12. QUALITY — polished refined look, high detail, crisp clean shapes

Minimum output length: 60 words. Maximum: 90 words.
NEVER use brand names: Pixar, Disney, DreamWorks, Illumination, Ghibli.
Use functional descriptors: 'premium 3D animated movie look', 'charming stylized character design'.
If living subject is present: NEVER add 'no people, no characters'.
"""
```

### Пример до / после (free-text)

**Ввод:** `«котик с цветами»`

**До:**
```
vibrant 3D cartoon render, adorable kitten holding flowers, cute, colorful,
warm cinematic lighting, high quality 3D render, 8k
```

**После:**
```
premium 3D animated movie look, charming stylized character design,
adorable fluffy kitten — single young animal, gender neutral —
with a warm joyful smile and big eyes with lively catchlights looking at the viewer,
gently cradling a bright spring bouquet in an endearing upright pose,
clean readable silhouette, strong character appeal, soft rounded shapes,
soft bright meadow background with gentle bokeh,
hero close-up framing, subject large in frame, clear focal point,
harmonious palette of fresh mint, peach blossom and soft butter-yellow,
soft three-point studio lighting with warm rim light and gentle fill,
smooth fluffy toy-like texture with subtle glossy highlights,
polished refined look, high detail, crisp clean shapes
```

---

## VIS-8 🟢 Визуальные guards для OpenAI Images

OpenAI Images API (gpt-image-1) игнорирует `negative_prompt`. Всё защитное
нужно класть в позитивный промпт. Добавить `OPENAI_VISUAL_GUARDS` (из VIS-6)
в хвост позитива перед `QUALITY_BLOCK` при вызове через OpenAI:

```python
def build_final_prompt(parts: list[str], api: str) -> str:
    if api == "openai":
        parts.append(OPENAI_VISUAL_GUARDS)
    parts.append(QUALITY_BLOCK)
    return ", ".join(p for p in parts if p)
```

---

## VIS-9 🟢 Brand-guard regex

**Проблема.** Ничего не блокирует появление брендов (Pixar/Disney/Ghibli) в промпте —
ни из ввода юзера, ни из GPT-вывода.

**Фикс.** Фильтр в `prompt_style.py`:

```python
import re

_BRAND_RE = re.compile(
    r"\b(pixar|disney|dreamworks|illumination|ghibli|in the style of)\b",
    re.IGNORECASE
)

def strip_brands(prompt: str) -> str:
    """Remove brand names that may cause refusals."""
    return _BRAND_RE.sub("", prompt).strip(", ")
```

Вызывать на выходе `assemble_prompt()` перед отправкой в API.

---

## Новая структура `assemble_prompt()`

Фиксированный порядок слоёв — единый для картинок и открыток:

```python
def assemble_prompt(
    tile_id: str,
    style_key: str,         # "3d_cartoon" | "scene_cozy" | "scene_epic" | "anime" | "realistic"
    subject: str,           # из паспорта: age+gender+number+category
    action: str | None,
    scene: str,
    is_card: bool = False,
    text_zone: str = "top", # для открыток
) -> tuple[str, str]:       # (positive, negative)

    is_living = style_key == "3d_cartoon"

    layers = [
        STYLE_ANCHORS[style_key],           # 1. стилевой анкор
        subject,                            # 2. идентичность (паспорт)
        get_expression(tile_id) if is_living else None,  # 3. мимика + взгляд
        get_pose(tile_id)       if is_living else None,  # 4. поза
        APPEAL_BLOCK            if is_living else None,  # 5. силуэт + обаяние
        action,                             # 6. действие
        scene,                              # 7. фон
        get_composition(tile_id, style_key),# 8. кадрирование + глубина
        get_palette(tile_id, is_living),    # 9. палитра
        get_light(tile_id),                 # 10. тип света
        CARD_LAYOUT if is_card else None,   # 11. открытковый слой (text_zone)
        QUALITY_BLOCK,                      # 12. polished / high detail
        TECHNICAL_BLOCK,                    # 13. рендер
    ]

    positive = ", ".join(x for x in layers if x)
    positive = strip_brands(positive)

    return positive, NEGATIVE_PROMPT


QUALITY_BLOCK = (
    "polished refined animated look, high detail, crisp clean shapes, "
    "vivid harmonious colors, cinematic composition"
)

CARD_LAYOUT = (
    "greeting card composition, generous clean space in upper third for text overlay, "
    "no text and no letters rendered in the image, "
    "harmonious balanced layout, soft uncluttered area for lettering"
)
```

---

## Примеры: до / после

### birthday — «поздравь подругу Аню с 50-летием»

**Паспорт (из GENERATION_FIXES.md):**
```
animacy=living, gender=female, age=elderly, number=single
WHO_CHAR → "kindly elderly woman"
```

**До:**
```
vibrant 3D cartoon render, modern animated feature film look, big expressive friendly eyes,
joyful birthday celebration scene,
adorable kindly elderly woman named Aня celebrating 50 birthday,
surrounded by colorful balloons and pastel confetti,
cozy festive setting with warm glowing candles and layered birthday cake,
cheerful bright atmosphere, warm golden hour glow, genuine joyful expression,
[LAYOUT_TEXT], [TECHNICAL] | [NEGATIVE]
```
Почему слабо: адресат — безликий болванчик без мимики/позы; цвет случайный; взгляда нет.

**После (с визуальным слоем):**
```
vibrant 3D cartoon render, big expressive eyes with lively catchlights,
premium stylized character design with strong character appeal, clean readable silhouette,
kindly elderly woman named Anya,
dignified proud warm expression, kind eyes with a gentle knowing smile looking at the viewer,
dignified upright pose with a gracious welcoming gesture,
clean readable silhouette, soft rounded appealing shapes, charismatic likeable personality,
joyful birthday celebration, surrounded by colorful balloons and soft confetti,
elegant layered birthday cake with warm glowing candles, soft golden-lit festive setting,
hero framing, subject large in frame, soft bokeh background, text space in upper third,
rich gold, champagne cream and warm ivory palette, prestigious warm tones,
warm intimate candlelight glow, soft orange-gold light, no harsh shadows,
bright toy-like materials with rich micro-texture,
greeting card layout, clean upper third for text overlay, no text rendered in image,
polished refined animated look, high detail, crisp clean shapes,
[TECHNICAL] | [NEGATIVE+]
```

---

### cartoon-char — «волшебница в звёздной мантии»

**До:**
```
vibrant 3D cartoon render, expressive wizard character with big friendly eyes,
waving hello with a big warm smile in cozy magical village at golden hour,
[TECHNICAL]
```

**После:**
```
vibrant 3D cartoon render, big expressive eyes with lively catchlights,
premium stylized character design with strong character appeal,
enchanting young female wizard in a deep blue star-studded cloak,
warm genuine beaming smile, sparkling eyes with lively catchlights looking at the viewer,
arms raised mid-spell in a dynamic joyful confident pose,
clean readable silhouette, soft rounded appealing shapes,
casting sparkles in a magical moonlit forest village,
hero medium shot, subject centered and large in frame, soft background bokeh,
palette: deep midnight blue, sparkling silver-white and warm gold accents,
soft magical inner glow, ethereal light rays with bokeh particles,
bright toy-like materials with rich micro-texture,
polished refined animated look, high detail, crisp clean shapes,
[TECHNICAL] | [NEGATIVE+]
```

---

## Затрагиваемые файлы

| Файл | Что меняем |
|---|---|
| `prompt_style.py` | VIS-1 (`STYLE_3D`), VIS-2 (`PALETTES`), VIS-4 (`COMPOSITION`), VIS-5 (`LIGHT`), VIS-6 (`NEGATIVE`), VIS-9 (`strip_brands`) |
| `card_prompts.py` | VIS-3 (`EXPRESSION`/`POSE`), передача `tile_id` в `assemble_prompt` |
| `picture_prompts.py` | VIS-3 (для персонажных тайлов), VIS-8 (OpenAI guards) |
| `gpt.py` | VIS-7 (скелет обогащения free-text) |
| `content_gen.py` | вызов нового `assemble_prompt(tile_id, ...)` вместо старого |

---

## Порядок внедрения

1. **P0:** VIS-1 (appeal в STYLE_3D) + VIS-2 (палитры) + VIS-3 (expression/pose) —
   дают немедленный визуальный прыжок
2. **P1:** VIS-4 (composition) + VIS-5 (light) + VIS-6 (NEGATIVE)
3. **P1:** VIS-7 (скелет free-text)
4. **P2:** VIS-8 + VIS-9

Каждый фикс изолирован, можно катить по одному. P0-тройка — один PR.

---

## Чек-лист: финальная проверка промпта

- [ ] Анкор: `premium 3D animated movie look` или сценный, без брендов (Pixar/Disney)
- [ ] Персонаж: паспорт субъекта (живое/род/число/возраст)
- [ ] Expression: конкретная эмоция + взгляд в камеру (для открыток обязательно)
- [ ] Pose: конкретная поза / body language (не «стоит»)
- [ ] Appeal: `clean silhouette, strong character appeal, rounded shapes`
- [ ] Палитра: конкретные цвета (не `bold warm saturated`)
- [ ] Свет: конкретный тип (`warm studio` / `golden hour` / `magical glow` / `candlelight`)
- [ ] Composition: hero-framing, focal point, bokeh
- [ ] NEGATIVE: включает `stiff pose, dead eyes, muddy colors, subject too small`
- [ ] OpenAI: визуальные guards в позитиве, а не в negative
- [ ] Brand-guard: нет Pixar/Disney/Ghibli
- [ ] Открытки: `no text rendered in image`, чистая text-zone
- [ ] Живое: нет `no people, no characters`
- [ ] Сцена без персонажа: есть `no people, no characters`

---

## Связанные файлы

- [GENERATION_FIXES.md](./GENERATION_FIXES.md) — фиксы семантических признаков (паспорт субъекта, род, число, возраст, _has_living_subject)
- [arteki/docs/prompt-semantic-attributes.md](./arteki/docs/prompt-semantic-attributes.md) — канонический источник паспорта
- [arteki/docs/prompt-templates-pictures.md](./arteki/docs/prompt-templates-pictures.md) — шаблоны 6 картинок
- [arteki/docs/prompt-templates-cards.md](./arteki/docs/prompt-templates-cards.md) — шаблоны 15 открыток
