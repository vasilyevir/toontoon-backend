"""Per-tile prompt templates for the 6 picture tiles (spec: prompt-templates-pictures).

Each picture tile has a fixed TEMPLATE with {PLACEHOLDER} slots. A small LLM
(GPT-4o mini) translates the user's answers to English, fills the placeholders
(using defaults when missing) and returns ``prompt | negative prompt``.

Two modes:
* STRUCTURED — all answers came from quick-reply buttons.
* FREE_TEXT  — the user typed a free-form description (extract meaning first).

Style anchors (unified branded cartoon 3D):
* living subjects (characters, animals, birds, fish) -> STYLE_3D
* inanimate scenes (landscape, food)                 -> SCENE_COZY / SCENE_EPIC
  (both carry "no people, no characters" so characters never wander in).

The style anchor and Technical block are baked into each template, so the
returned text is the complete positive prompt (no extra wrapping needed).
Brand names (Pixar/Disney) are intentionally avoided — blocked by some models.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.services.prompt_style import (
    NEGATIVE_PROMPT, APPEAL_BLOCK,
    get_expression, get_pose, get_palette, get_light, get_composition,
)

# ─── Shared anchors / blocks (mirror prompt_style + spec) ─────────────────────
_STYLE_3D = (
    "vibrant 3D cartoon render, modern animated feature film look, "
    "big expressive eyes with lively catchlights, "
    "soft rounded chunky shapes, smooth glossy surfaces with subtle texture, "
    "bold warm saturated colors, "
    "premium stylized character design with strong character appeal, "
    "clean readable silhouette, soft rounded appealing shapes, "
    "charismatic likeable personality, "
    "bright toy-like materials with rich micro-texture,"
)
_SCENE_COZY = (
    "cozy stylized 3D cartoon render, modern animated feature film look, "
    "soft rounded chunky toy-like shapes, charming miniature diorama aesthetic, "
    "richly detailed tactile materials with visible texture (wood grain, stone, fabric), "
    "warm naturalistic saturated colors, lush detailed environment, no people, no characters,"
)
_SCENE_EPIC = (
    "epic stylized 3D cartoon landscape render, modern animated feature film look, "
    "bold saturated colors, dramatic depth and scale, lush detailed environment, "
    "no people, no characters, majestic cinematic atmosphere,"
)
_TECHNICAL = (
    "Technical: warm cinematic lighting with soft rim light and gentle sun rays, soft natural shadows, "
    "glossy smooth cartoon materials with rich surface texture (not flat, not matte plastic), "
    "ray-traced GI, high quality 3D render, 8k resolution, "
    "crisp sharp details, clean anti-aliased edges, soft depth of field with gentle background bokeh"
)
_LAYOUT_CENTER = "subject centered in frame, soft bokeh background, clean uncluttered composition,"

# Picture tiles with living subjects (get expression + pose injection).
_LIVING_PICTURE_TILES: frozenset[str] = frozenset({"cartoon_character", "cute_animal", "birds", "fish"})
# Marker at end of _STYLE_3D — injection point for expression/pose/appeal.
_STYLE_END_MARKER = "bright toy-like materials with rich micro-texture,"


def _enrich_picture_template(tpl: "PictureTemplate") -> str:
    """Inject per-tile visual layers into the template at instruction-build time."""
    is_living = tpl.tile_id in _LIVING_PICTURE_TILES
    template = tpl.template

    # Insert expression + pose + appeal after style anchor (living tiles only).
    if is_living and _STYLE_END_MARKER in template:
        inject = f"{get_expression(tpl.tile_id)}, {get_pose(tpl.tile_id)}, {APPEAL_BLOCK},"
        template = template.replace(_STYLE_END_MARKER, f"{_STYLE_END_MARKER} {inject}", 1)

    # Replace generic layout center with enhanced composition + palette + light.
    style_key = "3d_cartoon" if is_living else "scene_cozy"
    comp = get_composition(tpl.tile_id, style_key, is_card=False)
    palette = get_palette(tpl.tile_id, is_living)
    light = get_light(tpl.tile_id)
    inject_layout = f"{comp}, {palette}, {light},"
    if _LAYOUT_CENTER in template:
        template = template.replace(_LAYOUT_CENTER, inject_layout, 1)

    return template


@dataclass(frozen=True)
class PictureTemplate:
    tile_id: str
    # placeholder name -> default value ("" means required, no default)
    fields: dict[str, str]
    template: str
    # short hints for the FREE_TEXT extractor (placeholder -> what to extract)
    extract_hints: dict[str, str] = field(default_factory=dict)
    # optional extra rules (e.g. landscape cozy/epic anchor swap)
    notes: tuple[str, ...] = ()


# ─── Templates (keyed by our backend tile ids) ────────────────────────────────

TEMPLATES: dict[str, PictureTemplate] = {
    # 1. Cartoon character (living -> STYLE_3D)
    "cartoon_character": PictureTemplate(
        tile_id="cartoon_character",
        fields={
            "CHARACTER_TYPE": "friendly adventurer",
            "ACTION": "waving hello with a big warm smile",
            "SETTING": "cozy magical village at golden hour",
        },
        template=(
            f"{_STYLE_3D} "
            "expressive {CHARACTER_TYPE} character with big friendly eyes and genuine smile, "
            "{ACTION} in {SETTING}, "
            "warm charming personality, heartwarming moment, "
            "cheerful bright atmosphere, warm golden hour glow, genuine joyful expression, "
            f"{_LAYOUT_CENTER} "
            f"{_TECHNICAL}"
        ),
        extract_hints={
            "CHARACTER_TYPE": 'role + appearance (e.g. "little wizard in blue hat")',
            "ACTION": "what character is doing",
            "SETTING": "environment",
        },
    ),
    # 2. Cute animal (living -> STYLE_3D)
    "cute_animal": PictureTemplate(
        tile_id="cute_animal",
        fields={
            "ANIMAL": "",  # required
            "ACTION": "sitting with gentle curious expression",
            "SETTING": "cozy warm home with soft blanket",
        },
        template=(
            f"{_STYLE_3D} "
            "adorable {ANIMAL} character {ACTION} in {SETTING}, "
            "soft fur with rich detailed texture, genuinely heartwarming and sweet expression, "
            "tender loving atmosphere, soft warm light, emotional connection, warm atmosphere of love and care, "
            f"{_LAYOUT_CENTER} "
            f"{_TECHNICAL}"
        ),
        extract_hints={
            "ANIMAL": 'type + appearance (e.g. "fluffy orange tabby kitten")',
            "ACTION": "what it does",
            "SETTING": "environment",
        },
    ),
    # 3. Birds (living -> STYLE_3D)
    "birds": PictureTemplate(
        tile_id="birds",
        fields={
            "BIRD": "cute round little songbird with soft fluffy feathers",
            "ACTION": "perched and singing cheerfully",
            "SETTING": "blooming garden branch in warm morning light",
        },
        template=(
            f"{_STYLE_3D} "
            "adorable {BIRD} character {ACTION} in {SETTING}, "
            "soft fluffy feathers with rich detailed texture, genuinely sweet cheerful expression, "
            "heartwarming tender atmosphere, soft warm light, gentle charm and lively energy, "
            f"{_LAYOUT_CENTER} "
            f"{_TECHNICAL}"
        ),
        extract_hints={
            "BIRD": 'type + appearance (e.g. "tiny blue robin", "round owl with big eyes")',
            "ACTION": "what it does",
            "SETTING": "environment",
        },
    ),
    # 4. Fish (living -> STYLE_3D)
    "fish": PictureTemplate(
        tile_id="fish",
        fields={
            "FISH": "colorful little tropical fish",
            "ACTION": "swimming playfully",
            "SETTING": "vibrant coral reef with soft sun rays through clear water",
        },
        template=(
            f"{_STYLE_3D} "
            "adorable {FISH} character {ACTION} in {SETTING}, "
            "glossy iridescent scales with rich detailed texture, sweet cheerful expression, "
            "underwater scene with soft sun rays through water, gentle floating bubbles, dreamy aquatic atmosphere, "
            f"{_LAYOUT_CENTER} "
            f"{_TECHNICAL}"
        ),
        extract_hints={
            "FISH": 'type + appearance (e.g. "orange clownfish", "round pufferfish")',
            "ACTION": "what it does",
            "SETTING": "underwater environment",
        },
    ),
    # 5. Landscape (inanimate -> SCENE_COZY, swap to EPIC for grand places)
    "nature": PictureTemplate(
        tile_id="nature",
        fields={
            "PLACE": "green forest meadow with rolling hills",
            "TIME_OF_DAY": "golden hour sunset",
            "DETAIL": "wildflowers and a small winding path with gentle mist",
        },
        template=(
            f"{_SCENE_COZY} "
            "breathtaking {PLACE} at {TIME_OF_DAY}, "
            "{DETAIL}, "
            "natural light matching the time of day, soft sun rays and soft natural shadows, serene inviting atmosphere, "
            f"{_LAYOUT_CENTER} "
            f"{_TECHNICAL}"
        ),
        extract_hints={
            "PLACE": "type of landscape",
            "TIME_OF_DAY": "lighting moment",
            "DETAIL": "special visual element",
        },
        notes=(
            "STYLE ANCHOR: if the place is grand/dramatic (mountains, ocean cliffs, canyon, "
            "vast valley, waterfalls) replace the opening cozy anchor with this EPIC anchor: "
            f"{_SCENE_EPIC} — otherwise keep the cozy anchor. Never add people or characters.",
        ),
    ),
    # 6. Food (inanimate object -> SCENE_COZY)
    "food": PictureTemplate(
        tile_id="food",
        fields={
            "DISH": "",  # required
            "SETTING": "cozy rustic wooden table near a sunny window",
        },
        template=(
            f"{_SCENE_COZY} "
            "delicious appetizing {DISH}, on {SETTING}, "
            "richly detailed tactile food textures (glossy, fresh, mouth-watering), "
            "soft steam and gentle highlights, fresh and inviting, warm golden light, cozy homemade atmosphere, "
            f"{_LAYOUT_CENTER} "
            f"{_TECHNICAL}"
        ),
        extract_hints={
            "DISH": 'food + appearance (e.g. "stack of pancakes with syrup", "bowl of ramen")',
            "SETTING": "where it's served",
        },
    ),
}


def is_picture_tile(tile_id: str | None) -> bool:
    return bool(tile_id) and tile_id in TEMPLATES


def _defaults_block(tpl: PictureTemplate) -> str:
    lines = []
    for name, default in tpl.fields.items():
        lines.append(f"{name}: {default or '(required — infer from the answers)'}")
    return "\n".join(lines)


def _notes_block(tpl: PictureTemplate) -> str:
    return ("\n\nSPECIAL RULES:\n" + "\n".join(tpl.notes)) if tpl.notes else ""


_STYLE_LOCK = (
    "STYLE LOCK: The opening style anchor in the TEMPLATE is fixed brand style. "
    "Do NOT change it, even if the user's text requests a different style (watercolor, "
    "realistic, oil paint, etc.). Only fill the scene {PLACEHOLDERS}."
)


def build_structured_instruction(tpl: PictureTemplate, answers: dict[str, str]) -> str:
    """Instruction for when the user answered with quick-reply buttons."""
    answer_lines = "\n".join(f"{k.upper()}: {v}" for k, v in answers.items() if v) or "(no answers)"
    enriched = _enrich_picture_template(tpl)
    return (
        "Translate the answers to English and fill the template.\n"
        "Each ANSWER KEY matches a {PLACEHOLDER} in the template. If a placeholder has no "
        "matching answer, use its default. Keep every fixed block EXACTLY as written.\n"
        f"{_STYLE_LOCK}\n"
        "Do not add any words from this banned list: Pixar, Disney, realistic, beautiful, "
        "high quality, perfect.\n"
        "Output ONLY one line: final prompt | negative prompt\n\n"
        f"ANSWERS:\n{answer_lines}\n\n"
        f"DEFAULTS:\n{_defaults_block(tpl)}"
        f"{_notes_block(tpl)}\n\n"
        f"TEMPLATE:\n{enriched}\n\n"
        f"NEGATIVE: {NEGATIVE_PROMPT}"
    )


def build_free_text_instruction(tpl: PictureTemplate, user_text: str) -> str:
    """Instruction for when the user typed a free-form description."""
    extract_lines = "\n".join(
        f"-> {name}: {tpl.extract_hints.get(name, name.lower())} (default: {default or 'infer'})"
        for name, default in tpl.fields.items()
    )
    enriched = _enrich_picture_template(tpl)
    return (
        "The user described the scene in free text (often Russian). Extract the meaning, "
        "use the defaults when unclear, and translate everything to English.\n"
        "Fill the template, keeping every fixed block EXACTLY as written.\n"
        f"{_STYLE_LOCK}\n"
        "Do not add any words from this banned list: Pixar, Disney, realistic, beautiful, "
        "high quality, perfect.\n"
        "Output ONLY one line: final prompt | negative prompt\n\n"
        f"USER TEXT: {user_text}\n\n"
        f"EXTRACT:\n{extract_lines}"
        f"{_notes_block(tpl)}\n\n"
        f"TEMPLATE:\n{enriched}\n\n"
        f"NEGATIVE: {NEGATIVE_PROMPT}"
    )
