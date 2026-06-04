"""Per-tile prompt templates for the 15 card tiles (spec: prompt-templates-cards).

Same engine as picture_prompts: a small LLM fills the fixed per-card TEMPLATE
from the user's answers and returns ``prompt | negative prompt``.

Cards always use LAYOUT_TEXT (clean space in the upper third for an overlaid
greeting). The ``text`` answer is mood context ONLY — it is rendered as a real
text overlay later, never drawn by the image generator.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.services.prompt_style import NEGATIVE_PROMPT

# Shared block strings (kept identical to the spec — unified branded cartoon 3D).
# Style fix round 2: warm cinematic textured look, no flat/matte; watercolor removed.
_STYLE_3D = (
    "vibrant 3D cartoon render, modern animated feature film look, big expressive friendly eyes, "
    "soft rounded chunky shapes, smooth glossy surfaces with subtle texture, "
    "bold warm saturated colors, cheerful charming character design, bright modern animation quality,"
)
_LAYOUT_TECHNICAL = (
    "generous negative space in upper third for text overlay, centered composition, rule of thirds, "
    "high contrast between subject and soft bokeh background, no busy patterns behind text areas, clean uncluttered layout, "
    "NO text NO words NO letters NO writing NO captions NO inscriptions NO labels in the image — image background only, "
    "Technical: warm cinematic lighting with soft rim light and gentle sun rays, soft natural shadows, "
    "glossy smooth cartoon materials with rich surface texture (not flat, not matte plastic), "
    "ray-traced global illumination, high quality 3D render, 8k resolution, "
    "crisp sharp details, clean anti-aliased edges, soft depth of field with gentle background bokeh"
)

# Postcard-specific negative prompt: text is rendered as a CSS overlay — the image must be text-free.
_CARD_NEGATIVE_EXTRA = (
    "text, words, letters, writing, captions, inscriptions, labels, typography, "
    "greeting text, birthday text, name text, any written characters, "
    "calligraphy, handwriting, printed text, overlay text, watermark, "
)


@dataclass(frozen=True)
class CardTemplate:
    tile_id: str
    fields: dict[str, str]  # placeholder -> default ("" means omit)
    template: str
    rules: tuple[str, ...] = ()
    extract_hints: dict[str, str] = field(default_factory=dict)


TEMPLATES: dict[str, CardTemplate] = {
    "birthday": CardTemplate(
        tile_id="birthday",
        fields={"WHO_CHAR": "cheerful adult", "NAME_PART": "(empty)", "AGE_DETAIL": "(empty)"},
        template=(
            f"{_STYLE_3D} "
            "joyful birthday celebration scene, "
            "adorable {WHO_CHAR} character{NAME_PART} surrounded by colorful balloons and pastel confetti, "
            "{AGE_DETAIL}cozy festive setting with warm glowing candles and layered birthday cake, "
            "cheerful bright atmosphere, warm golden hour glow, genuine joyful expression, "
            f"{_LAYOUT_TECHNICAL}"
        ),
        rules=(
            "{WHO_CHAR}: if age <= 12 or the recipient is a child -> 'young child', otherwise 'cheerful adult'",
            "{NAME_PART}: if a name is given -> ' named {name}', otherwise empty",
            "{AGE_DETAIL}: if age is given -> 'celebrating {age} birthday, ', otherwise empty",
        ),
        extract_hints={
            "WHO_CHAR": "adult or child character (infer from age)",
            "NAME_PART": "recipient's name if mentioned",
            "AGE_DETAIL": "age if mentioned",
        },
    ),
    "jubilee": CardTemplate(
        tile_id="jubilee",
        fields={"AGE": "milestone", "NAME_DETAIL": "(empty)"},
        template=(
            f"{_STYLE_3D} "
            "elegant {AGE} milestone celebration scene, "
            "luxurious golden decorations, champagne glasses with sparkling bubbles and soft rose petals, "
            "{NAME_DETAIL}prestigious festive setting, gold and cream color palette, soft shimmer, "
            "dignified and proud atmosphere, warm reverent lighting, heartfelt tribute, "
            f"{_LAYOUT_TECHNICAL}"
        ),
        rules=(
            "{AGE}: the milestone number/age if given, otherwise 'milestone'",
            "{NAME_DETAIL}: if a name is given -> 'a celebration for {name}, ', otherwise empty",
        ),
        extract_hints={"AGE": "milestone number", "NAME_DETAIL": "name if mentioned"},
    ),
    "valentine": CardTemplate(
        tile_id="valentine",
        fields={"SCENE_ELEMENTS": "lush red roses and glowing golden heart"},
        template=(
            f"{_STYLE_3D} "
            "dreamy Valentine's Day scene, "
            "{SCENE_ELEMENTS}, soft warm candlelight glow, delicate pink and red rose petals floating, "
            "romantic tender atmosphere, warm candlelight glow, love and togetherness, gentle dreaminess, "
            "soft gold and rose color palette, sparkling hearts in the background, "
            f"{_LAYOUT_TECHNICAL}"
        ),
        rules=(
            "{SCENE_ELEMENTS}: expand the chosen scene into rich romantic objects "
            "(roses -> 'lush red roses', hearts -> 'glowing golden hearts', "
            "chocolate -> 'elegant box of chocolates', teddy bear -> 'cuddly teddy bear')",
        ),
        extract_hints={"SCENE_ELEMENTS": "romantic visual objects"},
    ),
    "wedding": CardTemplate(
        tile_id="wedding",
        fields={
            "STYLE_DETAIL": "dreamy soft",
            "SCENE_ELEMENTS": "white roses and golden wedding rings",
            "NAME_DETAIL": "(empty)",
        },
        template=(
            f"{_STYLE_3D} "
            "{STYLE_DETAIL} wedding celebration scene, "
            "{SCENE_ELEMENTS}, delicate lace patterns and soft floating petals, "
            "{NAME_DETAIL}romantic tender atmosphere, warm candlelight glow, love and togetherness, gentle dreaminess, "
            "soft gold and white color palette, ethereal soft focus background, "
            f"{_LAYOUT_TECHNICAL}"
        ),
        rules=(
            "{STYLE_DETAIL}: delicate -> 'dreamy soft', grand -> 'elegant prestigious', fairy-tale -> 'enchanted magical'",
            "{SCENE_ELEMENTS}: expand scene (flowers -> 'white roses and peonies', rings -> 'golden wedding rings', doves -> 'white doves')",
            "{NAME_DETAIL}: if names are given -> 'a celebration for {names}, ', otherwise empty",
        ),
        extract_hints={
            "STYLE_DETAIL": "dreamy, elegant, or enchanted",
            "SCENE_ELEMENTS": "key visual objects",
            "NAME_DETAIL": "couple names if mentioned",
        },
    ),
    "anniversary": CardTemplate(
        tile_id="anniversary",
        fields={"YEARS_DETAIL": "(empty)"},
        template=(
            f"{_STYLE_3D} "
            "{YEARS_DETAIL}romantic anniversary scene, "
            "rich red roses and glowing golden hearts, warm candlelight reflections on satin ribbon, "
            "intimate cozy setting with soft sparkles and delicate petals, "
            "romantic tender atmosphere, warm candlelight glow, love and togetherness, gentle dreaminess, "
            f"{_LAYOUT_TECHNICAL}"
        ),
        rules=("{YEARS_DETAIL}: if years are given -> '{years} anniversary, ', otherwise empty",),
        extract_hints={"YEARS_DETAIL": "how many years together if mentioned"},
    ),
    "mothers_day": CardTemplate(
        tile_id="mothers_day",
        fields={"SCENE_ELEMENTS": "pink peonies and white daisies", "NAME_DETAIL": "(empty)"},
        template=(
            f"{_STYLE_3D} "
            "gentle Mother's Day bouquet of {SCENE_ELEMENTS}, "
            "{NAME_DETAIL}soft golden morning light, warmth of unconditional love and care, "
            "delicate translucent flower petals, soft blush and cream color palette, "
            "tender loving atmosphere, soft warm light, emotional connection, warm atmosphere of love and care, "
            f"{_LAYOUT_TECHNICAL}"
        ),
        rules=(
            "{SCENE_ELEMENTS}: expand the chosen flowers/symbols "
            "(flowers -> 'pink peonies and white daisies', heart -> 'soft floral heart', nature -> 'fresh spring blossoms')",
            "{NAME_DETAIL}: if a name is given -> 'a tribute to {name}, ', otherwise empty",
        ),
        extract_hints={"SCENE_ELEMENTS": "flowers or loving symbols", "NAME_DETAIL": "mom's name if mentioned"},
    ),
    "fathers_day": CardTemplate(
        tile_id="fathers_day",
        fields={
            "SCENE_DETAIL": "peaceful outdoor nature scene with warm golden light",
            "NAME_DETAIL": "(empty)",
        },
        template=(
            f"{_STYLE_3D} "
            "heartwarming Father's Day scene, "
            "{SCENE_DETAIL}, warm golden afternoon light filtering through trees, "
            "{NAME_DETAIL}proud and loving atmosphere, strong and gentle character energy, "
            "warm golden tones, confident and heartfelt setting, "
            f"{_LAYOUT_TECHNICAL}"
        ),
        rules=(
            "{SCENE_DETAIL}: nature -> 'lush green outdoor setting with tall trees', "
            "sports -> 'sporting scene with cheerful energy', fishing -> 'peaceful lakeside fishing scene', "
            "tools -> 'clean workshop with neatly arranged tools'",
            "{NAME_DETAIL}: if a name is given -> 'a celebration for {name}, ', otherwise empty",
        ),
        extract_hints={"SCENE_DETAIL": "father's activity or setting", "NAME_DETAIL": "dad's name if mentioned"},
    ),
    "easter": CardTemplate(
        tile_id="easter",
        fields={"SCENE_ELEMENTS": "colorful hand-painted eggs and fluffy baby chick"},
        template=(
            f"{_STYLE_3D} "
            "joyful Easter spring celebration scene, "
            "{SCENE_ELEMENTS} in a soft wicker basket with fresh spring flowers and bright green grass, "
            "warm golden sunrise light, delicate pastel yellow and green tones, "
            "cheerful bright atmosphere, warm golden hour glow, genuine joyful expression, "
            f"{_LAYOUT_TECHNICAL}"
        ),
        rules=(
            "{SCENE_ELEMENTS}: expand scene (Easter eggs -> 'colorful hand-painted eggs', "
            "spring flowers -> 'fresh spring flowers', baby chick -> 'fluffy baby chick', bunny -> 'cute bunny')",
        ),
        extract_hints={"SCENE_ELEMENTS": "Easter visual elements"},
    ),
    "thanksgiving": CardTemplate(
        tile_id="thanksgiving",
        fields={"SCENE_ELEMENTS": "richly colored pumpkins and golden autumn leaves"},
        template=(
            f"{_STYLE_3D} "
            "cozy Thanksgiving autumn harvest scene, "
            "{SCENE_ELEMENTS}, warm amber and orange maple leaves gently falling, "
            "soft golden late afternoon light, rustic wooden table with harvest abundance, "
            "uplifting bright atmosphere, fresh morning energy, positive and hopeful vibes, clean vibrant light, "
            f"{_LAYOUT_TECHNICAL}"
        ),
        rules=(
            "{SCENE_ELEMENTS}: expand scene (pumpkins -> 'richly colored pumpkins', "
            "autumn leaves -> 'golden autumn leaves', harvest -> 'abundant harvest cornucopia', "
            "turkey -> 'roast turkey centerpiece')",
        ),
        extract_hints={"SCENE_ELEMENTS": "autumn harvest elements"},
    ),
    "new_year": CardTemplate(
        tile_id="new_year",
        fields={"SCENE_ELEMENTS": "beautifully decorated Christmas tree with colorful lights and golden star"},
        template=(
            f"{_STYLE_3D} "
            "magical winter holiday celebration scene, "
            "{SCENE_ELEMENTS}, fresh snow gently falling, cozy warm glow of holiday lights, "
            "soft gold and silver sparkles, festive winter night sky with stars, "
            "magical enchanting atmosphere, dreamy soft light, sparkles and gentle wonder, whimsical fairy-tale glow, "
            f"{_LAYOUT_TECHNICAL}"
        ),
        rules=(
            "{SCENE_ELEMENTS}: expand scene (Christmas tree -> 'beautifully decorated Christmas tree with colorful lights and golden star', "
            "Santa -> 'cheerful Santa Claus', snow -> 'gentle falling snow over cozy houses', "
            "holiday animals -> 'cute holiday animals')",
        ),
        extract_hints={"SCENE_ELEMENTS": "holiday visual subjects"},
    ),
    "graduation": CardTemplate(
        tile_id="graduation",
        fields={"SCENE_ELEMENTS": "graduation cap diploma scroll and gold stars", "NAME_DETAIL": "(empty)"},
        template=(
            f"{_STYLE_3D} "
            "proud graduation celebration scene, "
            "{SCENE_ELEMENTS}, golden stars and colorful confetti bursting with joy, "
            "{NAME_DETAIL}warm and proud atmosphere, bright achievement energy, "
            "rich purple and gold color palette, triumphant and heartwarming moment, "
            f"{_LAYOUT_TECHNICAL}"
        ),
        rules=(
            "{SCENE_ELEMENTS}: expand scene (diploma & cap -> 'graduation cap and diploma scroll', "
            "confetti -> 'bursting colorful confetti', flowers -> 'celebration bouquet', books -> 'stack of books')",
            "{NAME_DETAIL}: if a name is given -> \"celebrating {name}'s achievement, \", otherwise empty",
        ),
        extract_hints={"SCENE_ELEMENTS": "graduation visuals", "NAME_DETAIL": "graduate's name if mentioned"},
    ),
    "get_well": CardTemplate(
        tile_id="get_well",
        fields={"SCENE_ELEMENTS": "bright sunflowers with gentle butterflies and warm golden light"},
        template=(
            f"{_STYLE_3D} "
            "cheerful healing and care scene, "
            "{SCENE_ELEMENTS}, gentle warm sunlight streaming through soft white clouds, "
            "soft yellow and green tones, gentle butterflies and fresh spring air, "
            "uplifting bright atmosphere, fresh morning energy, positive and hopeful vibes, clean vibrant light, "
            f"{_LAYOUT_TECHNICAL}"
        ),
        rules=(
            "{SCENE_ELEMENTS}: expand scene (sunflowers -> 'bright sunflowers', sunshine -> 'warm radiant sunshine', "
            "birds -> 'cheerful little birds', rainbow -> 'soft colorful rainbow')",
        ),
        extract_hints={"SCENE_ELEMENTS": "cheerful healing visuals"},
    ),
    "just_because": CardTemplate(
        tile_id="just_because",
        fields={"SCENE_ELEMENTS": "bright bouquet of wildflowers with a cheerful gift bow"},
        template=(
            f"{_STYLE_3D} "
            "cheerful spontaneous surprise scene, "
            "{SCENE_ELEMENTS}, colorful confetti bursting with joy, bright cheerful ribbon and soft petals, "
            "unexpected heartwarming moment, warmth of genuine friendship and care, "
            "uplifting bright atmosphere, fresh morning energy, positive and hopeful vibes, clean vibrant light, "
            f"{_LAYOUT_TECHNICAL}"
        ),
        rules=(
            "{SCENE_ELEMENTS}: expand scene (flowers -> 'bright bouquet of wildflowers', nature -> 'fresh nature scene', "
            "animal -> 'cute friendly animal', surprise -> 'wrapped surprise gift')",
        ),
        extract_hints={"SCENE_ELEMENTS": "main visual"},
    ),
    # Moved from the picture branch — now greeting cards (carry an overlaid text).
    "good_morning": CardTemplate(
        tile_id="good_morning",
        fields={
            "SUBJECT": "cute character with gentle smile",
            "ACTIVITY": "enjoying cup of tea on sunny balcony",
        },
        template=(
            f"{_STYLE_3D} "
            "serene peaceful morning scene, "
            "{SUBJECT} enjoying {ACTIVITY}, "
            "soft warm morning light streaming through window, gentle sunrise rays, cozy awakening atmosphere, "
            "peaceful serene mood, warm dawn colors, heartwarming mindful moment, "
            f"{_LAYOUT_TECHNICAL}"
        ),
        rules=(
            "{SUBJECT}: who or what is the focus (default: cute character with gentle smile)",
            "{ACTIVITY}: a calm morning activity (default: enjoying cup of tea on sunny balcony)",
        ),
        extract_hints={
            "SUBJECT": "who or what is the focus",
            "ACTIVITY": "morning activity",
        },
    ),
    "good_day": CardTemplate(
        tile_id="good_day",
        fields={
            "SUBJECT": "cheerful friendly character",
            "SETTING": "sunny garden with blooming flowers and blue sky",
        },
        template=(
            f"{_STYLE_3D} "
            "bright cheerful daytime scene, "
            "{SUBJECT} in {SETTING}, fresh golden sunlight, "
            "uplifting bright atmosphere, fresh positive energy, hopeful vibes, clean vibrant light, "
            "genuine joyful expression, heartwarming moment, "
            f"{_LAYOUT_TECHNICAL}"
        ),
        rules=(
            "{SUBJECT}: the main subject (default: cheerful friendly character)",
            "{SETTING}: a daytime environment (default: sunny garden with blooming flowers and blue sky)",
        ),
        extract_hints={
            "SUBJECT": "main subject",
            "SETTING": "daytime environment",
        },
    ),
}


def is_card_tile(tile_id: str | None) -> bool:
    return bool(tile_id) and tile_id in TEMPLATES


def _defaults_block(tpl: CardTemplate) -> str:
    return "\n".join(f"{name}: {default or '(omit)'}" for name, default in tpl.fields.items())


def _rules_block(tpl: CardTemplate) -> str:
    return ("\n\nPLACEHOLDER RULES:\n" + "\n".join(tpl.rules)) if tpl.rules else ""


_PREAMBLE = (
    "Keep every fixed block EXACTLY as written. Resolve each {PLACEHOLDER} using the answers "
    "and the rules below; if a value is missing, use the default or omit as instructed.\n"
    "STYLE LOCK: The opening style anchor is fixed brand style — do NOT change it, even if "
    "the user requests a different style (watercolor, realistic, etc.). Only fill placeholders.\n"
    "TEXT LOCK: NEVER draw, spell, render, or include ANY text, letters, words, names, numbers, "
    "captions, inscriptions or typographic elements in the image. The greeting will be added as "
    "a separate CSS text overlay — the image must be 100% text-free.\n"
    "Do not use any of these words: Pixar, Disney, realistic, beautiful, high quality, perfect.\n"
    "Output ONLY one line: final prompt | negative prompt"
)


def build_structured_instruction(tpl: CardTemplate, answers: dict[str, str]) -> str:
    answer_lines = "\n".join(f"{k}: {v}" for k, v in answers.items() if v) or "(no answers)"
    full_negative = f"{_CARD_NEGATIVE_EXTRA}{NEGATIVE_PROMPT}"
    return (
        "Translate the answers to English and fill the template.\n"
        f"{_PREAMBLE}\n\n"
        f"ANSWERS:\n{answer_lines}\n\n"
        f"DEFAULTS:\n{_defaults_block(tpl)}"
        f"{_rules_block(tpl)}\n\n"
        f"TEMPLATE:\n{tpl.template}\n\n"
        f"NEGATIVE: {full_negative}"
    )


def build_free_text_instruction(tpl: CardTemplate, user_text: str) -> str:
    extract_lines = "\n".join(
        f"-> {name}: {tpl.extract_hints.get(name, name.lower())} (default: {default or 'omit'})"
        for name, default in tpl.fields.items()
    )
    full_negative = f"{_CARD_NEGATIVE_EXTRA}{NEGATIVE_PROMPT}"
    return (
        "The user described the card in free text (often Russian). Extract the meaning, use the "
        "defaults when unclear, and translate everything to English.\n"
        f"{_PREAMBLE}\n\n"
        f"USER TEXT: {user_text}\n\n"
        f"EXTRACT:\n{extract_lines}"
        f"{_rules_block(tpl)}\n\n"
        f"TEMPLATE:\n{tpl.template}\n\n"
        f"NEGATIVE: {full_negative}"
    )
