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

# Each style preset: an anchor (placed first) and a technical tail (placed last).
PRESETS: dict[str, dict[str, str]] = {
    "3d_cartoon": {
        "anchor": (
            "3D stylized render, Pixar/Disney emotional character design, "
            "soft clay-like rounded forms, warm pastel color palette"
        ),
        "technical": (
            "Technical: subsurface scattering on skin and materials, soft diffused "
            "studio lighting, ray-traced global illumination, matte ceramic material, "
            "physically based rendering (PBR), 8k resolution, crisp details, anti-aliased edges"
        ),
    },
    "watercolor": {
        "anchor": (
            "watercolor illustration style, soft color bleeding edges, visible paper "
            "texture, gentle fluid brush strokes, dreamy atmospheric perspective, "
            "delicate translucent layers, hand-painted aesthetic, soft pastel washes"
        ),
        "technical": (
            "Technical: smooth high-resolution finish, clean crisp details, "
            "gentle natural light, harmonious composition"
        ),
    },
    "pastel_flat": {
        "anchor": (
            "flat vector illustration style, pastel color palette, soft rounded corners, "
            "minimal gentle shading, clean graphic design aesthetic, smooth gradients, "
            "2D cartoon style, simple shapes, harmonious color blocks"
        ),
        "technical": (
            "Technical: clean vector edges, smooth gradients, high resolution, crisp readable shapes"
        ),
    },
    "storybook": {
        "anchor": (
            "children's storybook illustration style, whimsical fairy tale aesthetic, "
            "rich detailed background, storytelling composition, magical enchanting "
            "atmosphere, classic book illustration technique, warm inviting colors"
        ),
        "technical": (
            "Technical: painterly detailed rendering, soft diffused lighting, rich textures, "
            "8k resolution, crisp details"
        ),
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
    "realistic": {
        "anchor": (
            "soft cinematic 3D render, gently stylized realism, warm natural color palette, "
            "lifelike but friendly characters"
        ),
        "technical": (
            "Technical: subsurface scattering, soft diffused lighting, ray-traced global "
            "illumination, physically based rendering (PBR), 8k resolution, crisp details"
        ),
    },
}

# Layout block — added for tiles that carry a greeting/announcement text so the
# generator leaves a clean area for the overlaid text (which we never render as
# letters, because generators garble text).
LAYOUT_BLOCK = (
    "generous negative space in the upper third reserved for a text overlay, "
    "centered composition following the rule of thirds, high contrast between subject "
    "and a soft bokeh background, no busy patterns behind text areas, clean uncluttered layout"
)

# Negative prompt — for generators that support it (FLUX via fal/Replicate, SDXL).
# Pollinations' URL API does not, so it is kept here for the next generator.
NEGATIVE_PROMPT = (
    "distorted anatomy, photorealistic horror, uncanny valley faces, creepy expressions, "
    "messy cluttered background, harsh aggressive neon colors, small unreadable text, "
    "gibberish text, watermarks, signatures, cropped limbs, blurry faces, extra fingers, "
    "extra limbs, deformed hands, low quality, jpeg artifacts, scary dark atmosphere"
)

# Categories whose tiles place a text/greeting on the image.
_TEXT_CATEGORIES = {"postcard", "announcement"}

# Map a user-facing style label (or tile style answer) to a preset key.
_STYLE_MAP = {
    "cartoon": "3d_cartoon",
    "3d": "3d_cartoon",
    "3d cartoon": "3d_cartoon",
    "pixar": "3d_cartoon",
    "disney": "3d_cartoon",
    "watercolor": "watercolor",
    "aquarelle": "watercolor",
    "pastel": "pastel_flat",
    "pastel flat": "pastel_flat",
    "flat": "pastel_flat",
    "fairytale": "storybook",
    "fairy tale": "storybook",
    "storybook": "storybook",
    "japanese": "anime",
    "anime": "anime",
    "ghibli": "anime",
    "realistic": "realistic",
    "realism": "realistic",
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
