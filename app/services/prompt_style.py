"""Prompt assembly building blocks — implements the "expensive look" methodology
from the product spec (ТЗ §4–6).

A final prompt is always assembled as:

    [STYLE ANCHOR] , [SCENE] (, [LAYOUT for text tiles]) , [TECHNICAL]

The SCENE is produced either by GPT (the "smart builder") or, as a fallback, by
the mechanical builder. The deterministic blocks below guarantee the prompt
always starts with the style anchor and ends with the technical block, and that
the banned words are never appended by us.
"""
from __future__ import annotations

import re

DEFAULT_STYLE = "3d_cartoon"

# Single brand technical tail — warm cinematic, textured, NOT flat/matte
# (style fix round 2: removed matte ceramic / subsurface / PBR / soft diffused).
_TECHNICAL = (
    "Technical: warm cinematic lighting with soft rim light and gentle sun rays, "
    "soft natural shadows, glossy smooth cartoon materials with rich surface texture "
    "(not flat, not matte plastic), ray-traced global illumination, high quality 3D render, "
    "8k resolution, crisp sharp details, clean anti-aliased edges, "
    "soft depth of field with gentle background bokeh"
)

# Each style preset: an anchor (placed first) and a technical tail (placed last).
# The brand look is a single vibrant cartoon 3D; flat styles (watercolor,
# pastel_flat, storybook) were removed — everything maps to the 3D family.
PRESETS: dict[str, dict[str, str]] = {
    # Living characters + all cards: the unified branded cartoon 3D.
    "3d_cartoon": {
        # Brand names (Pixar/Disney) are blocked by some models — use descriptive terms.
        "anchor": (
            "vibrant 3D cartoon render, modern animated feature film look, "
            "big expressive eyes with lively catchlights, "
            "soft rounded chunky shapes, smooth glossy surfaces with subtle texture, "
            "bold warm saturated colors, "
            "premium stylized character design with strong character appeal, "
            "clean readable silhouette, soft rounded appealing shapes, "
            "charismatic likeable personality, "
            "bright toy-like materials with rich micro-texture"
        ),
        "technical": _TECHNICAL,
    },
    # Inanimate / cosy scenes: nature, magical forest, objects (hasLiving = false).
    "scene_cozy": {
        "anchor": (
            "cozy stylized 3D cartoon render, modern animated feature film look, "
            "soft rounded chunky toy-like shapes, charming miniature diorama aesthetic, "
            "richly detailed tactile materials with visible texture (wood grain, stone, fabric), "
            "warm naturalistic saturated colors, lush detailed environment, "
            "no people, no characters, inviting heartwarming atmosphere"
        ),
        "technical": _TECHNICAL,
    },
    # Inanimate / epic scenes: mountains, vast landscapes (hasLiving = false).
    "scene_epic": {
        "anchor": (
            "epic stylized 3D cartoon landscape render, modern animated feature film look, "
            "bold saturated colors, dramatic depth and scale, lush detailed environment, "
            "no people, no characters, majestic cinematic atmosphere"
        ),
        "technical": _TECHNICAL,
    },
    "anime": {
        "anchor": (
            "anime illustration style, clean cel shading, expressive large eyes, "
            "vibrant yet soft color palette, detailed hand-drawn linework, "
            "Studio Ghibli inspired warmth"
        ),
        "technical": (
            "Technical: clean crisp lineart, soft cinematic lighting, high resolution, "
            "detailed background"
        ),
    },
    # Photorealistic / hyper-detailed style — for when the user explicitly wants realism.
    "realistic": {
        "anchor": (
            "hyperrealistic photographic render, cinematic DSLR shot, "
            "razor-sharp focus on subject, true-to-life proportions and materials, "
            "natural color grading, award-winning photography"
        ),
        "technical": (
            "Technical: golden hour directional light, gentle ambient occlusion, "
            "accurate material reflections and surface detail, "
            "shallow depth of field, 8k resolution, no artifacts"
        ),
    },
}

# Layout block for tiles that carry overlaid text (greetings/announcements):
# the generator leaves a clean area for text we add later (never rendered as letters).
LAYOUT_BLOCK = (
    "generous negative space in upper third for text overlay, centered composition, "
    "rule of thirds, high contrast between subject and soft bokeh background, "
    "no busy patterns behind text areas, clean uncluttered layout"
)

# Layout block for plain pictures with no overlaid text.
LAYOUT_CENTER = (
    "subject centered in frame, soft bokeh background, clean uncluttered composition"
)

# Negative prompt — passed to generators that support it (Pollinations FLUX,
# fal/Replicate, SDXL). OpenAI's Images API ignores it — use OPENAI_VISUAL_GUARDS instead.
NEGATIVE_PROMPT = (
    "distorted anatomy, photorealistic horror, uncanny valley faces, creepy expressions, "
    "dead eyes, blank stare, "
    "messy cluttered background, small unreadable text, "
    "watermarks, signatures, cropped limbs, blurry faces, extra fingers or limbs, "
    "low quality, jpeg artifacts, scary dark atmosphere, "
    "dull muted colors, washed out palette, grey tones, muddy dirty colors, oversaturated acidic neon, "
    "flat 2D illustration, flat vector art, matte plastic look, flat even lighting, "
    "stiff lifeless pose, flat dull expression, boring symmetrical static stance, "
    "subject too small in frame, bad framing, empty wasted composition, "
    "text, letters, words, captions, watermark, signature, logo"
)

# Categories whose tiles place a text/greeting on the image.
_TEXT_CATEGORIES = {"postcard", "announcement"}

# Map a user-facing style label (or tile style answer) to a preset key.
# Flat styles were removed — they now resolve to the unified branded 3D cartoon.
_STYLE_MAP = {
    "cartoon": "3d_cartoon",
    "cartoon 3d": "3d_cartoon",
    "3d": "3d_cartoon",
    "3d cartoon": "3d_cartoon",
    "cozy scene": "scene_cozy",
    "epic scene": "scene_epic",
    "pixar": "3d_cartoon",
    "pixart": "3d_cartoon",
    "disney": "3d_cartoon",
    "cinematic": "scene_epic",
    "3d animation": "3d_cartoon",
    "3d-animation": "3d_cartoon",
    "watercolor": "3d_cartoon",
    "aquarelle": "3d_cartoon",
    "pastel": "3d_cartoon",
    "pastel flat": "3d_cartoon",
    "flat": "3d_cartoon",
    "fairytale": "scene_cozy",
    "fairy tale": "scene_cozy",
    "storybook": "scene_cozy",
    "cozy": "scene_cozy",
    "epic": "scene_epic",
    "landscape": "scene_cozy",
    "japanese": "anime",
    "anime": "anime",
    "ghibli": "anime",
    "realistic": "realistic",
    "realism": "realistic",
    "photo": "realistic",
    "photorealistic": "realistic",
}


def map_style(style: str | None) -> str:
    """Resolve a free-text style label to a preset key (defaults to 3d_cartoon)."""
    if not style:
        return DEFAULT_STYLE
    return _STYLE_MAP.get(style.strip().lower(), DEFAULT_STYLE)


def is_text_tile(category: str | None) -> bool:
    return category in _TEXT_CATEGORIES if category else False


def assemble(scene: str, *, style_key: str, is_text: bool) -> str:
    """Wrap a scene description with the style anchor (first) and technical (last)."""
    preset = PRESETS.get(style_key, PRESETS[DEFAULT_STYLE])
    scene = scene.strip().strip(".").strip()
    parts = [preset["anchor"], scene]
    if is_text:
        parts.append(LAYOUT_BLOCK)
    parts.append(preset["technical"])
    return strip_brands(", ".join(p for p in parts if p))


# ─── Visual layer constants (VIS-1 through VIS-9) ────────────────────────────

APPEAL_BLOCK = (
    "clean readable silhouette, strong character appeal, "
    "charismatic and likeable, soft rounded appealing shapes"
)

QUALITY_BLOCK = (
    "polished refined animated look, high detail, crisp clean shapes, "
    "vivid harmonious colors, cinematic composition"
)

# For OpenAI Images API — that API ignores negative_prompt, so we embed guards in the positive.
OPENAI_VISUAL_GUARDS = (
    "clean correct anatomy with five fingers, friendly non-scary cartoon face, "
    "lively expressive eyes with bright catchlights, appealing dynamic natural pose, "
    "subject large and clear in frame, tidy uncluttered background, "
    "vivid clean harmonious colors, no muddy or washed out tones, "
    "NO text NO letters NO words NO watermark in the image, "
    "glossy textured surfaces (not flat, not matte plastic)"
)

# VIS-2 — Named palettes per occasion/type
PALETTES: dict[str, str] = {
    "birthday":     "harmonious palette of warm coral, soft cream and gold accents, vivid but not acidic",
    "jubilee":      "rich gold, champagne cream and warm ivory, prestigious warm tones",
    "graduation":   "rich purple, royal gold and warm white, triumphant and bright",
    "new_year":     "deep midnight blue, sparkling silver, warm gold and soft white glow",
    "valentine":    "soft rose pink, deep red and warm champagne gold, tender romantic palette",
    "anniversary":  "deep crimson, warm gold and soft cream, intimate and romantic",
    "mothers_day":  "soft blush pink, rose and champagne cream, delicate and tender",
    "wedding":      "pure white, soft ivory and warm gold with silver accents",
    "easter":       "pastel yellow, soft mint and light peach, gentle spring tones",
    "thanksgiving": "rich amber, burnt orange, cream and warm brown harvest tones",
    "fathers_day":  "teal, warm amber and natural green with golden sunlight accents",
    "get_well":     "bright sunny yellow, soft green and warm sky blue, uplifting and fresh",
    "good_morning": "warm peach, golden yellow and soft cream, gentle sunrise tones",
    "good_day":     "bright vivid warm palette, clean bold colors with harmonious contrast",
    "just_because": "bright cheerful multicolor palette, warm and inviting tones",
    "character":    "bright vivid warm palette, clean bold colors with harmonious contrast",
    "scene_cozy":   "warm naturalistic saturated colors, rich earthy and botanical tones",
    "scene_epic":   "bold dramatic deep tones, vivid contrast with majestic atmosphere",
}


def get_palette(tile_id: str, is_living: bool) -> str:
    if tile_id in PALETTES:
        return PALETTES[tile_id]
    return PALETTES["character"] if is_living else PALETTES["scene_cozy"]


# VIS-3 — Expression and gaze per tile
EXPRESSION: dict[str, str] = {
    "birthday":         "warm genuine beaming smile, sparkling eyes with lively catchlights looking at the viewer",
    "jubilee":          "dignified proud warm expression, kind eyes with a gentle knowing smile, looking at the viewer",
    "valentine":        "tender loving gaze, soft warm smile, eyes filled with warmth and affection, looking at viewer",
    "anniversary":      "tender loving gaze, gentle warm smile, full of warmth and affection",
    "mothers_day":      "gentle tender expression, soft loving eyes, warm nurturing presence, looking at viewer",
    "fathers_day":      "confident proud warm smile, strong gentle eyes, looking at the viewer",
    "wedding":          "radiant joyful expression, eyes sparkling with happiness, beaming warm smile",
    "graduation":       "proud triumphant smile, bright excited eyes with a sense of achievement",
    "easter":           "cheerful joyful smile, bright delighted eyes, playful expression",
    "new_year":         "excited magical expression, wide sparkling eyes full of wonder and joy",
    "get_well":         "warm hopeful gentle smile, kind caring eyes, uplifting and comforting presence",
    "thanksgiving":     "warm grateful expression, content happy smile, cozy and welcoming",
    "good_morning":     "peaceful serene gentle smile, soft warm eyes, calm awakening expression",
    "good_day":         "bright cheerful smile, energetic happy eyes, positive and inviting",
    "just_because":     "playful warm smile, delighted surprised eyes, spontaneous joyful energy",
}

# VIS-3 — Pose and body language per tile
POSE: dict[str, str] = {
    "birthday":         "arms raised mid-celebration in a joyful dynamic pose, mid-gesture",
    "jubilee":          "dignified upright pose with a gracious welcoming gesture",
    "valentine":        "gentle tender pose, hands near heart or holding flowers, soft and romantic",
    "mothers_day":      "graceful gentle pose, arms outstretched or holding bouquet, nurturing presence",
    "fathers_day":      "confident relaxed upright pose, one hand raised in a warm greeting",
    "graduation":       "triumphant pose with arms raised, graduation cap thrown in the air",
    "easter":           "playful leaning pose, hands holding Easter basket or decorated egg",
    "new_year":         "excited dynamic pose, arms raised with sparkles, mid-celebration",
    "get_well":         "gentle comforting soft pose, offering warmth and care",
    "good_morning":     "cozy relaxed sitting pose, holding a warm cup, peaceful and content",
    "good_day":         "upbeat confident walking or waving pose, full of energy",
    "just_because":     "spontaneous joyful pose, mid-laugh or mid-wave, light and playful",
    "_default":         "natural lively body language, mid-gesture, dynamic and expressive pose",
}


def get_expression(tile_id: str) -> str:
    return EXPRESSION.get(tile_id, "warm genuine smile, lively expressive eyes looking at the viewer")


def get_pose(tile_id: str) -> str:
    return POSE.get(tile_id, POSE["_default"])


# VIS-4 — Composition / framing modes
COMPOSITION: dict[str, str] = {
    "card_hero": (
        "hero framing, subject large in frame, eye-level, clear single focal point, "
        "soft background bokeh, layered depth with subject sharp and separated from background, "
        "balanced negative space in upper third for text overlay"
    ),
    "character": (
        "hero medium shot, subject centered and large in frame, eye-level, "
        "clear single focal point, soft background bokeh with gentle depth"
    ),
    "scene_cozy": (
        "intimate close-in framing, tidy composition, single clear focal object, "
        "rich but uncluttered layered depth, soft background bokeh"
    ),
    "scene_epic": (
        "wide establishing shot, dramatic horizon line, rule of thirds, "
        "bold foreground-midground-background depth layers, majestic scale"
    ),
    "food": (
        "appetizing overhead or 3/4 close-up, subject fills the frame, "
        "mouth-watering hero framing, clean surface, soft side lighting"
    ),
}


def get_composition(tile_id: str, style_key: str, *, is_card: bool = False) -> str:
    if tile_id == "food":
        return COMPOSITION["food"]
    if style_key == "scene_epic":
        return COMPOSITION["scene_epic"]
    if style_key == "scene_cozy":
        return COMPOSITION["scene_cozy"]
    if style_key == "3d_cartoon" and is_card:
        return COMPOSITION["card_hero"]
    return COMPOSITION["character"]


# VIS-5 — Light types
LIGHT: dict[str, str] = {
    "warm_studio": (
        "soft three-point studio lighting, warm key light with gentle rim light "
        "and soft bounce fill, no harsh shadows"
    ),
    "golden_hour": (
        "warm golden hour sunlight, long soft shadows, rich amber glow, "
        "gentle lens flare and warm atmospheric haze"
    ),
    "morning_soft": (
        "soft diffuse morning light, clean cool-to-warm gradient, "
        "gentle sunrise rays, peaceful and bright"
    ),
    "magical_glow": (
        "magical dreamy inner glow, soft sparkle light, ethereal light rays, "
        "luminous warm atmosphere with bokeh light particles"
    ),
    "candlelight": (
        "warm intimate candlelight, soft orange-gold glow, gentle flickering warmth, "
        "deep warm shadows, cozy romantic atmosphere"
    ),
    "daylight_bright": (
        "bright clean midday sunlight, vivid uplifting light, sharp natural shadows, "
        "fresh and energetic atmosphere"
    ),
}

_LIGHT_BY_TILE: dict[str, str] = {
    "birthday":         "warm_studio",
    "jubilee":          "candlelight",
    "valentine":        "candlelight",
    "anniversary":      "candlelight",
    "mothers_day":      "morning_soft",
    "fathers_day":      "golden_hour",
    "wedding":          "magical_glow",
    "graduation":       "warm_studio",
    "easter":           "morning_soft",
    "new_year":         "magical_glow",
    "get_well":         "daylight_bright",
    "thanksgiving":     "golden_hour",
    "good_morning":     "morning_soft",
    "good_day":         "daylight_bright",
    "just_because":     "daylight_bright",
    "nature":           "golden_hour",
    "food":             "warm_studio",
    "cartoon_character": "warm_studio",
    "cute_animal":      "warm_studio",
    "birds":            "morning_soft",
    "fish":             "magical_glow",
}


def get_light(tile_id: str) -> str:
    key = _LIGHT_BY_TILE.get(tile_id, "warm_studio")
    return LIGHT[key]


# VIS-9 — Brand guard: strip model/studio names that cause API refusals
_BRAND_RE = re.compile(
    r"\b(pixar|disney|dreamworks|illumination|ghibli|marvel|nintendo|"
    r"pok[eé]mon|nickelodeon|warner bros|looney tunes|sanrio|"
    r"in the style of)\b",
    re.IGNORECASE,
)


def strip_brands(prompt: str) -> str:
    """Remove brand names that may cause generation refusals."""
    return _BRAND_RE.sub("", prompt).strip(", ")


# ─── Named third-party IP guard (logic-fix 3.1 / 3.2) ────────────────────────
# We NEVER pass a copyrighted character name to the generator, and we NEVER let
# the assistant swap a requested character for a *different* brand. Known IP is
# paraphrased to a generic look; unknown IP is handled by the LLM system prompt
# plus the strip_brands() guard above.
_NAMED_IP_MAP: dict[str, str] = {
    "spongebob squarepants": "a cheerful yellow cartoon sea-sponge character",
    "spongebob": "a cheerful yellow cartoon sea-sponge character",
    "спанчбоб": "a cheerful yellow cartoon sea-sponge character",
    "спанч боб": "a cheerful yellow cartoon sea-sponge character",
    "губка боб": "a cheerful yellow cartoon sea-sponge character",
    "смешарики": "round colorful cartoon animal characters",
    "smeshariki": "round colorful cartoon animal characters",
    "mickey mouse": "a classic cheerful cartoon mouse character",
    "микки маус": "a classic cheerful cartoon mouse character",
    "elsa": "an ice-magic princess character in a sparkling blue gown",
    "эльза": "an ice-magic princess character in a sparkling blue gown",
    "spider-man": "a masked superhero in a red-and-blue suit",
    "spiderman": "a masked superhero in a red-and-blue suit",
    "человек-паук": "a masked superhero in a red-and-blue suit",
    "batman": "a masked superhero in a dark caped suit",
    "бэтмен": "a masked superhero in a dark caped suit",
    "pikachu": "a cute chubby yellow electric creature with red cheeks",
    "пикачу": "a cute chubby yellow electric creature with red cheeks",
    "hello kitty": "a cute white cartoon kitten character with a red bow",
    "shrek": "a friendly large green ogre character",
    "шрек": "a friendly large green ogre character",
    "minions": "small yellow cartoon helper characters in goggles",
    "minion": "a small yellow cartoon helper character in goggles",
    "миньоны": "small yellow cartoon helper characters in goggles",
    "миньон": "a small yellow cartoon helper character in goggles",
    "peppa pig": "a cute pink cartoon piglet character",
    "свинка пеппа": "a cute pink cartoon piglet character",
    "cheburashka": "a cute big-eared brown cartoon creature",
    "чебурашка": "a cute big-eared brown cartoon creature",
}

# Longest keys first so multi-word IP wins over its single-word prefix.
_NAMED_IP_RE = re.compile(
    "|".join(re.escape(k) for k in sorted(_NAMED_IP_MAP, key=len, reverse=True)),
    re.IGNORECASE,
)


def neutralize_ip(text: str | None) -> str | None:
    """Replace named third-party characters/franchises with a generic look."""
    if not text:
        return text
    return _NAMED_IP_RE.sub(lambda m: _NAMED_IP_MAP[m.group(0).lower()], text)
