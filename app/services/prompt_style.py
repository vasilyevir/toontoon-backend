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
            "big expressive friendly eyes, soft rounded chunky shapes, "
            "smooth glossy surfaces with subtle texture, bold warm saturated colors, "
            "cheerful charming character design, bright modern animation quality"
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
# fal/Replicate, SDXL). OpenAI's Images API ignores it.
NEGATIVE_PROMPT = (
    "distorted anatomy, photorealistic horror, uncanny valley faces, creepy expressions, "
    "messy cluttered background, small unreadable text, "
    "watermarks, signatures, cropped limbs, blurry faces, extra fingers or limbs, "
    "low quality, jpeg artifacts, scary dark atmosphere, "
    "dull muted colors, washed out palette, grey tones, "
    "flat 2D illustration, flat vector art, matte plastic look, flat even lighting"
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
    "disney": "3d_cartoon",
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
    return ", ".join(p for p in parts if p)
