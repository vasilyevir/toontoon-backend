"""Video prompt builder v2 — text-to-video architecture.

Implements prompt-templates-video-v2.md:
  • Style blocks: STYLE_3D, STYLE_3D_SCENE_COZY, STYLE_3D_SCENE_EPIC, TECHNICAL, NEGATIVE
  • Per-tile deterministic builders (Groups A, B, C)
  • Free-text categoriser + LLM prompt builder
  • build_storyboard() — main entry point

Formula: seedance_prompt = anchor + ". " + motion (+ " Audio: …" if audio enabled)
No keyframe images are generated — pure text-to-video.
Exception: Group C (photo tiles) → first_frame_url + motion only.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Optional

import httpx

from app.config import settings
from app.services import prompt_style

logger = logging.getLogger("toontoon.video_prompts")

STYLE_3D = "vibrant 3D cartoon render, modern animated feature film look, big expressive friendly eyes, soft rounded chunky shapes, smooth glossy surfaces with subtle texture, bold warm saturated colors, cheerful charming character design, bright modern animation quality"
STYLE_3D_SCENE_COZY = "cozy stylized 3D cartoon render, modern animated feature film look, soft rounded chunky toy-like shapes, charming miniature diorama aesthetic, richly detailed tactile materials with visible texture (wood grain, stone, fabric), warm naturalistic saturated colors, lush detailed environment, no people, no characters, inviting heartwarming atmosphere"
STYLE_3D_SCENE_EPIC = "epic stylized 3D cartoon landscape render, modern animated feature film look, bold saturated colors, dramatic depth and scale, lush detailed environment, no people, no characters, majestic cinematic atmosphere"
TECHNICAL = "Technical: warm cinematic lighting with soft rim light and gentle sun rays, soft natural shadows, glossy smooth cartoon materials with rich surface texture (not flat, not matte plastic), ray-traced GI, high quality 3D render, 8k resolution, crisp sharp details, clean anti-aliased edges, soft depth of field with gentle background bokeh"
LAYOUT_TEXT = "generous negative space in upper third for text overlay, centered composition, rule of thirds, high contrast between subject and soft bokeh background, no busy patterns behind text areas, clean uncluttered layout, no text and no letters rendered in the image"
LAYOUT_CENTER = "subject centered in frame, soft bokeh background, clean uncluttered composition"
NEGATIVE = "distorted anatomy, photorealistic horror, uncanny valley faces, creepy expressions, messy cluttered background, small unreadable text, watermarks, signatures, cropped limbs, blurry faces, extra fingers or limbs, low quality, jpeg artifacts, scary dark atmosphere, dull muted colors, washed out palette, grey tones, flat 2D illustration, flat vector art, matte plastic look, flat even lighting"
_FALLBACK_MOTION = "ambient gentle motion, very slow cinematic drift, subtle parallax depth, no morphing, no hard cuts, smooth natural looping movement"


@dataclass
class VideoStoryboard:
    anchor: str
    motion: str
    audio_enabled: bool = False
    audio_prompt: str = ""
    negative: str = NEGATIVE
    loop: bool = True
    duration: int = 8
    text_overlay: Optional[str] = None


def assemble_prompt(board: "VideoStoryboard") -> str:
    """Combine anchor + motion (+ audio hint) into a single Seedance text prompt."""
    parts = [board.anchor.rstrip(", "), board.motion.rstrip(". ")]
    p = ". ".join(x for x in parts if x)
    if board.audio_enabled and board.audio_prompt:
        p += f". Audio: {board.audio_prompt}"
    return p


_NATURE_PLACE: dict[str, tuple[str, str]] = {
    'лес': ('lush forest', 'cozy'),
    'forest': ('lush forest', 'cozy'),
    'море': ('wide ocean coastline', 'epic'),
    'sea': ('wide ocean coastline', 'epic'),
    'горы': ('majestic mountain range', 'epic'),
    'mountains': ('majestic mountain range', 'epic'),
    'поле': ('blooming open meadow', 'cozy'),
    'field': ('blooming open meadow', 'cozy'),
    '🌲 forest': ('lush forest', 'cozy'),
    '🌊 sea': ('wide ocean coastline', 'epic'),
    '⛰️ mountains': ('majestic mountain range', 'epic'),
    '🌾 field': ('blooming open meadow', 'cozy'),
}
_NATURE_TIME: dict[str, str] = {
    'летнее утро': 'summer morning with warm golden light',
    'summer morning': 'summer morning with warm golden light',
    'осенний закат': 'autumn sunset with amber and crimson hues',
    'autumn sunset': 'autumn sunset with amber and crimson hues',
    'зимняя ночь': 'winter night with soft moonlight on snow',
    'winter night': 'winter night with soft moonlight on snow',
    'весенний день': 'bright spring day with fresh greenery',
    'spring day': 'bright spring day with fresh greenery',
}
_NATURE_SPECIAL: dict[str, tuple[str, str]] = {
    'ветер': ('with a gentle breeze rustling leaves', 'leaves and grass swaying softly in the wind'),
    'wind': ('with a gentle breeze rustling leaves', 'leaves and grass swaying softly in the wind'),
    'туман': ('with soft morning mist drifting', 'soft mist slowly drifting through the scene'),
    'fog': ('with soft morning mist drifting', 'soft mist slowly drifting through the scene'),
    'снег': ('with soft snowflakes gently falling', 'snowflakes softly drifting downward'),
    'snow': ('with soft snowflakes gently falling', 'snowflakes softly drifting downward'),
    'цветение': ('with petals floating in the air', 'flower petals slowly drifting through the breeze'),
    'blossoming': ('with petals floating in the air', 'flower petals slowly drifting through the breeze'),
    'blossom': ('with petals floating in the air', 'flower petals slowly drifting through the breeze'),
}
_NATURE_MOOD: dict[str, str] = {
    'спокойное': 'serene peaceful mood',
    'calm': 'serene peaceful mood',
    'волшебное': 'magical enchanted atmosphere',
    'magical': 'magical enchanted atmosphere',
    'яркое': 'vivid vibrant lively energy',
    'vivid': 'vivid vibrant lively energy',
}
_NATURE_AUDIO: dict[str, str] = {
    'звуки природы': 'soft natural ambience, gentle wind and rustling leaves, birds in distance',
    'nature sounds': 'soft natural ambience, gentle wind and rustling leaves, birds in distance',
    'спокойная мелодия': 'calm gentle ambient instrumental music, soft and soothing, no vocals',
    'calm music': 'calm gentle ambient instrumental music, soft and soothing, no vocals',
    'calm melody': 'calm gentle ambient instrumental music, soft and soothing, no vocals',
    'тишина': '',
    'silence': '',
}
_MORNING_SCENE: dict[str, str] = {
    'рассвет': 'warm sunrise over a cozy meadow',
    'sunrise': 'warm sunrise over a cozy meadow',
    'цветы': 'blooming spring flowers in soft morning light',
    'flowers': 'blooming spring flowers in soft morning light',
    'чашка кофе': 'a cozy steaming cup of coffee on a wooden table',
    'cup of coffee': 'a cozy steaming cup of coffee on a wooden table',
    'окно с видом': 'a window with a warm sunny morning view',
    'window': 'a window with a warm sunny morning view',
}
_MORNING_SPECIAL: dict[str, tuple[str, str]] = {
    'лучи солнца': ('soft golden sun rays', 'warm sun rays gently shifting'),
    'sun rays': ('soft golden sun rays', 'warm sun rays gently shifting'),
    'лёгкий туман': ('soft morning mist', 'soft mist slowly drifting'),
    'light fog': ('soft morning mist', 'soft mist slowly drifting'),
    'распускаются цветы': ('flowers slowly blooming', 'flowers gently blooming'),
    'blooming flowers': ('flowers slowly blooming', 'flowers gently blooming'),
    'пылинки света': ('dust motes of light floating', 'dust motes of light floating softly'),
    'light dust': ('dust motes of light floating', 'dust motes of light floating softly'),
    'floating light': ('dust motes of light floating', 'dust motes of light floating softly'),
}
_MORNING_AUDIO: dict[str, str] = {
    'спокойная мелодия': 'calm gentle ambient instrumental music, soft and soothing, no vocals',
    'calm melody': 'calm gentle ambient instrumental music, soft and soothing, no vocals',
    'звуки утра': 'soft morning ambience, gentle birdsong, distant nature sounds',
    'morning sounds': 'soft morning ambience, gentle birdsong, distant nature sounds',
    'morning birds': 'soft morning ambience, gentle birdsong, distant nature sounds',
    'тишина': '',
    'silence': '',
}
_INSPIRING_SCENE: dict[str, tuple[str, str]] = {
    'солнечный пейзаж': ('bright sunny daytime landscape', 'cozy'),
    'sunny landscape': ('bright sunny daytime landscape', 'cozy'),
    'цветущее поле': ('blooming wildflower field', 'cozy'),
    'blooming field': ('blooming wildflower field', 'cozy'),
    'море': ('shimmering ocean at golden hour', 'epic'),
    'sea': ('shimmering ocean at golden hour', 'epic'),
    'город днём': ('cheerful sunlit city street', 'cozy'),
    'city': ('cheerful sunlit city street', 'cozy'),
}
_INSPIRING_SPECIAL: dict[str, tuple[str, str]] = {
    'лёгкий ветер': ('a soft breeze', 'gentle breeze with swaying grass and leaves'),
    'light wind': ('a soft breeze', 'gentle breeze with swaying grass and leaves'),
    'light breeze': ('a soft breeze', 'gentle breeze with swaying grass and leaves'),
    'солнечные блики': ('warm sun glints', 'sunlight glinting and shimmering gently'),
    'sun glints': ('warm sun glints', 'sunlight glinting and shimmering gently'),
    'парящие лепестки': ('drifting flower petals', 'flower petals drifting through warm air'),
    'floating petals': ('drifting flower petals', 'flower petals drifting through warm air'),
    'облака плывут': ('drifting clouds', 'clouds drifting slowly across a bright sky'),
    'drifting clouds': ('drifting clouds', 'clouds drifting slowly across a bright sky'),
}
_INSPIRING_AUDIO: dict[str, str] = {
    'лёгкая позитивная музыка': 'light upbeat positive instrumental music, cheerful and warm, no vocals',
    'light positive music': 'light upbeat positive instrumental music, cheerful and warm, no vocals',
    'light upbeat music': 'light upbeat positive instrumental music, cheerful and warm, no vocals',
    'звуки природы': 'soft natural ambience, gentle breeze and distant birdsong',
    'nature sounds': 'soft natural ambience, gentle breeze and distant birdsong',
    'тишина': '',
    'silence': '',
}
_ANIMAL_MAP: dict[str, str] = {
    'котёнок': 'adorable kitten',
    'кот': 'charming cat',
    'кошка': 'charming cat',
    'cat': 'adorable kitten',
    'kitten': 'adorable kitten',
    'щенок': 'playful puppy',
    'собака': 'friendly dog',
    'dog': 'playful puppy',
    'puppy': 'playful puppy',
    'кролик': 'fluffy rabbit',
    'rabbit': 'fluffy rabbit',
    'bunny': 'fluffy rabbit',
    'лисёнок': 'cute little fox',
    'fox': 'cute little fox',
    'fox cub': 'cute little fox',
}
_ANIMAL_ACTION: dict[str, tuple[str, str]] = {
    'сидит и моргает': ('sitting with a gentle curious expression', 'slow blinking and calm breathing'),
    'sitting': ('sitting with a gentle curious expression', 'slow blinking and calm breathing'),
    'sitting & blinking': ('sitting with a gentle curious expression', 'slow blinking and calm breathing'),
    'играет': ('playfully engaged with a toy', 'playful little bounces and paw swipes'),
    'playing': ('playfully engaged with a toy', 'playful little bounces and paw swipes'),
    'умывается': ('gently grooming itself', 'gently grooming, paw moving to face'),
    'grooming': ('gently grooming itself', 'gently grooming, paw moving to face'),
    'виляет хвостом': ('sitting happily with tail wagging', 'softly wagging tail, ears perking up'),
    'wagging': ('sitting happily with tail wagging', 'softly wagging tail, ears perking up'),
    'wagging tail': ('sitting happily with tail wagging', 'softly wagging tail, ears perking up'),
    'sleeping': ('sleeping peacefully on a soft surface', 'soft breathing and gentle ear twitches'),
    'спит': ('sleeping peacefully on a soft surface', 'soft breathing and gentle ear twitches'),
}
_ANIMAL_SETTING: dict[str, str] = {
    'дома на пледе': 'cozy warm home with a soft plaid blanket',
    'at home': 'cozy warm home with a soft plaid blanket',
    'on a cozy blanket': 'cozy warm home with a soft plaid blanket',
    'в саду': 'sunny garden with soft green grass',
    'in garden': 'sunny garden with soft green grass',
    'in the garden': 'sunny garden with soft green grass',
    'на подоконнике': 'a warm sunlit windowsill',
    'on windowsill': 'a warm sunlit windowsill',
    'on the windowsill': 'a warm sunlit windowsill',
    'в цветах': 'surrounded by blooming spring flowers',
    'in flowers': 'surrounded by blooming spring flowers',
    'among flowers': 'surrounded by blooming spring flowers',
}
_ANIMAL_AUDIO: dict[str, str] = {
    'голос животного': '',
    'animal sound': '',
    'милая мелодия': 'cute soft playful instrumental music, light and cheerful, no vocals',
    'cute melody': 'cute soft playful instrumental music, light and cheerful, no vocals',
    'звуки природы': 'soft natural ambience, gentle outdoor sounds',
    'nature sounds': 'soft natural ambience, gentle outdoor sounds',
    'тишина': '',
    'silence': '',
}
_CARTOON_CHARACTER: dict[str, str] = {
    'волшебник': 'wizard',
    'мага': 'wizard',
    'wizard': 'wizard',
    'рыцарь': 'knight',
    'knight': 'knight',
    'фея': 'fairy',
    'fairy': 'fairy',
    'робот': 'robot',
    'robot': 'robot',
    'человек': 'friendly adventurer',
    'human': 'friendly adventurer',
    'животное': 'charming animal character',
    'animal': 'charming animal character',
    'существо': 'friendly magical creature',
    'creature': 'friendly magical creature',
}
_CARTOON_ACTION: dict[str, tuple[str, str]] = {
    'машет рукой': ('standing with arm lowered, warm smile', 'raises arm in a warm friendly wave'),
    'waving': ('standing with arm lowered, warm smile', 'raises arm in a warm friendly wave'),
    'танцует': ('in a playful starting pose', 'breaks into joyful expressive dancing'),
    'dancing': ('in a playful starting pose', 'breaks into joyful expressive dancing'),
    'колдует': ('holding a glowing spell with focused eyes', 'casts a magical sparkle burst'),
    'casting': ('holding a glowing spell with focused eyes', 'casts a magical sparkle burst'),
    'прыгает от радости': ('poised with a big grin', 'leaps into a joyful bounce with arms up'),
    'jumping': ('poised with a big grin', 'leaps into a joyful bounce with arms up'),
}
_CARTOON_SETTING: dict[str, str] = {
    'в деревне': 'cozy magical village at golden hour',
    'village': 'cozy magical village at golden hour',
    'in a village': 'cozy magical village at golden hour',
    'в лесу': 'enchanted forest with glowing fireflies',
    'forest': 'enchanted forest with glowing fireflies',
    'in a forest': 'enchanted forest with glowing fireflies',
    'в городе': 'sunny cheerful city street',
    'city': 'sunny cheerful city street',
    'in the city': 'sunny cheerful city street',
    'в замке': 'warm-lit fantasy castle interior',
    'castle': 'warm-lit fantasy castle interior',
    'in a castle': 'warm-lit fantasy castle interior',
}
_CARTOON_AUDIO: dict[str, str] = {
    'весёлая музыка': 'upbeat cheerful playful instrumental music, no vocals',
    'fun music': 'upbeat cheerful playful instrumental music, no vocals',
    'волшебные звуки': 'magical sparkly chimes and gentle whooshes',
    'magical sounds': 'magical sparkly chimes and gentle whooshes',
    'тишина': '',
    'silence': '',
}
_GREETING_AUDIO: dict[str, str] = {
    'праздничная музыка': 'upbeat warm celebratory music, festive and joyful, no vocals',
    'festive music': 'upbeat warm celebratory music, festive and joyful, no vocals',
    'тёплая мелодия': 'gentle warm heartfelt instrumental melody, no vocals',
    'warm melody': 'gentle warm heartfelt instrumental melody, no vocals',
    'тишина': '',
    'silence': '',
}
_OCCASION_DATA: dict[str, tuple[str, str, str]] = {
    'birthday': ('Birthday', 'balloons, flowers and confetti, a festive cake, warm golden glow', 'Happy Birthday!'),
    'milestone birthday': ('Milestone Birthday', 'golden balloons, champagne and elegant roses, luxurious golden celebration', 'Happy Birthday!'),
    "valentine's day": ("Valentine's Day", 'red roses and floating hearts, soft pink-and-red romantic glow', "Happy Valentine's Day!"),
    'wedding': ('Wedding', 'white roses and soft petals, gold accents, dreamy soft light', 'Congratulations!'),
    'anniversary': ('Anniversary', 'red roses and hearts, warm candlelight, soft golden tones', 'Happy Anniversary!'),
    "mother's day": ("Mother's Day", 'soft pink peonies and white daisies, gentle morning light', "Happy Mother's Day!"),
    "father's day": ("Father's Day", 'warm outdoor nature, golden sunlight, cozy proud mood', "Happy Father's Day!"),
    'easter': ('Easter', 'colorful painted eggs in a nest, spring flowers, a baby chick', 'Happy Easter!'),
    'thanksgiving': ('Thanksgiving', 'autumn harvest with pumpkins, acorns and orange maple leaves, cozy amber fall', 'Happy Thanksgiving!'),
    'new year / christmas': ('New Year / Christmas', 'a decorated Christmas tree with lights, falling snow, golden-and-blue magic', 'Happy Holidays!'),
    'graduation': ('Graduation', 'a graduation cap and diploma scroll, gold stars and confetti, purple-and-gold', 'Congratulations, Grad!'),
    'get well soon': ('Get Well Soon', 'bright sunflowers and butterflies, warm sunshine, uplifting healing warmth', 'Get Well Soon!'),
    'just because': ('Just Because', 'colorful flowers and confetti, a cheerful surprise, bright spontaneous joy', 'Thinking of You'),
}


def _strip_leading_emoji(s: str) -> str:
    """Strip leading emoji / symbol characters (non-alphanumeric) from a string.

    e.g. "🌲 Forest" → "forest", "😊 Warm smile" → "warm smile"
    """
    i = 0
    while i < len(s) and not s[i].isalnum():
        i += 1
    return s[i:].strip()


def _lookup(mapping: dict, key: str, default=None):
    """Case-insensitive key lookup; also tries after stripping leading emoji."""
    if not key:
        return default
    k = key.strip().lower()
    v = mapping.get(k)
    if v is not None:
        return v
    v = mapping.get(_strip_leading_emoji(k))
    return v if v is not None else default


def _build_nature_video(answers: dict[str, str], free_text: str | None) -> VideoStoryboard:
    place_raw = answers.get("place") or "forest"
    place_key = _strip_leading_emoji(place_raw.lower())
    place_desc, scale = _NATURE_PLACE.get(place_key, ("serene natural landscape", "cozy"))

    time_raw = answers.get("time") or answers.get("time_season") or "summer morning"
    time_key = _strip_leading_emoji(time_raw.lower())
    time_desc = _lookup(_NATURE_TIME, time_key, "a peaceful time of day")

    special_raw = answers.get("special") or "wind"
    special_key = _strip_leading_emoji(special_raw.lower())
    special_air, special_motion = _NATURE_SPECIAL.get(
        special_key, ("with soft ambient movement", "gentle natural motion")
    )

    mood_raw = answers.get("mood") or "calm"
    mood_key = _strip_leading_emoji(mood_raw.lower())
    mood_desc = _lookup(_NATURE_MOOD, mood_key, "serene peaceful mood")

    audio_raw = answers.get("audio") or "nature sounds"
    audio_key = _strip_leading_emoji(audio_raw.lower())
    audio_prompt = _lookup(_NATURE_AUDIO, audio_key, "")

    style = STYLE_3D_SCENE_EPIC if scale == "epic" else STYLE_3D_SCENE_COZY
    anchor = f"{style}, cinematic {place_desc}, {time_desc}, {special_air}, {mood_desc}, atmospheric depth of field, wide establishing shot, balanced natural composition, {LAYOUT_CENTER}, {TECHNICAL}"
    motion = f"ambient motion only — {special_motion}, very slow camera drift with subtle parallax depth, no subject morphing, natural seamless looping movement"
    return VideoStoryboard(anchor=anchor, motion=motion, audio_enabled=bool(audio_prompt), audio_prompt=audio_prompt, loop=True, duration=8)


def _build_morning_video(answers: dict[str, str], free_text: str | None) -> VideoStoryboard:
    scene_raw = answers.get("scene") or answers.get("subject") or "sunrise"
    scene_key = _strip_leading_emoji(scene_raw.lower())
    scene_desc = _lookup(_MORNING_SCENE, scene_key, "warm cozy morning scene")

    special_raw = answers.get("special") or answers.get("light") or "sun rays"
    special_key = _strip_leading_emoji(special_raw.lower())
    special_air, special_motion = _MORNING_SPECIAL.get(
        special_key, ("soft warm light", "soft warm light gently shifting")
    )

    text_val = answers.get("text") or "Good morning!"

    audio_raw = answers.get("audio") or "calm melody"
    audio_key = _strip_leading_emoji(audio_raw.lower())
    audio_prompt = _lookup(_MORNING_AUDIO, audio_key, "calm gentle ambient music, no vocals")

    anchor = f"{STYLE_3D_SCENE_COZY}, gentle warm morning scene with {scene_desc}, soft golden sunrise light, with {special_air}, fresh uplifting mood, {LAYOUT_TEXT}, {TECHNICAL}"
    motion = f"gentle ambient morning motion — {special_motion}, soft warm light slowly warming up, very slow cinematic push-in, no subject morphing, smooth and calm"
    return VideoStoryboard(anchor=anchor, motion=motion, audio_enabled=bool(audio_prompt), audio_prompt=audio_prompt, loop=False, duration=8, text_overlay=text_val)


def _build_inspiring_video(answers: dict[str, str], free_text: str | None) -> VideoStoryboard:
    scene_raw = answers.get("scene") or answers.get("subject") or "sunny landscape"
    scene_key = _strip_leading_emoji(scene_raw.lower())
    scene_desc, scale = _INSPIRING_SCENE.get(scene_key, ("bright sunny daytime landscape", "cozy"))

    special_raw = answers.get("special") or answers.get("light") or "light breeze"
    special_key = _strip_leading_emoji(special_raw.lower())
    special_air, special_motion = _INSPIRING_SPECIAL.get(
        special_key, ("soft breeze", "gentle breeze swaying the scene")
    )

    text_val = answers.get("text") or "Have a great day!"

    audio_raw = answers.get("audio") or "light upbeat music"
    audio_key = _strip_leading_emoji(audio_raw.lower())
    audio_prompt = _lookup(_INSPIRING_AUDIO, audio_key, "light upbeat instrumental music, no vocals")

    style = STYLE_3D_SCENE_EPIC if scale == "epic" else STYLE_3D_SCENE_COZY
    anchor = f"{style}, bright cheerful daytime scene, {scene_desc}, warm sunny daylight, with {special_air}, uplifting positive mood, vivid warm colors, {LAYOUT_TEXT}, {TECHNICAL}"
    motion = f"gentle uplifting ambient motion — {special_motion}, warm daylight shimmering, very slow camera drift with parallax, no subject morphing, smooth and bright"
    return VideoStoryboard(anchor=anchor, motion=motion, audio_enabled=bool(audio_prompt), audio_prompt=audio_prompt, loop=False, duration=8, text_overlay=text_val)


def _build_cute_animal_video(answers: dict[str, str], free_text: str | None) -> VideoStoryboard:
    animal_raw = answers.get("animal") or "kitten"
    animal_key = _strip_leading_emoji(animal_raw.lower())
    animal_desc = _lookup(_ANIMAL_MAP, animal_key, animal_key)

    action_raw = answers.get("action") or "sitting & blinking"
    action_key = _strip_leading_emoji(action_raw.lower())
    action_start, action_motion = _ANIMAL_ACTION.get(action_key, ("sitting calmly", "gentle calm motion"))

    setting_raw = answers.get("setting") or answers.get("place") or "on a cozy blanket"
    setting_key = _strip_leading_emoji(setting_raw.lower())
    setting_desc = _lookup(_ANIMAL_SETTING, setting_key, "cozy warm home")

    audio_raw = answers.get("audio") or "animal sound"
    audio_key = _strip_leading_emoji(audio_raw.lower())
    if "голос" in audio_key or "animal sound" in audio_key or "voice" in audio_key:
        if "кот" in animal_key or "cat" in animal_key:
            audio_prompt = "a soft gentle kitten meow, plus light cozy ambience"
        elif "соб" in animal_key or "пёс" in animal_key or "щен" in animal_key or "dog" in animal_key:
            audio_prompt = "a soft friendly puppy yip, plus light cozy ambience"
        else:
            audio_prompt = "a soft gentle animal sound, plus light cozy ambience"
        audio_enabled = True
    else:
        audio_prompt = _lookup(_ANIMAL_AUDIO, audio_raw.lower(), "")
        if not audio_prompt:
            audio_prompt = _lookup(_ANIMAL_AUDIO, audio_key, "")
        audio_enabled = bool(audio_prompt)

    anchor = f"{STYLE_3D}, adorable {animal_desc} {action_start} in {setting_desc}, soft fur with rich detailed texture, genuinely heartwarming sweet expression, tender loving atmosphere, soft warm light, {LAYOUT_CENTER}, {TECHNICAL}"
    motion = f"gentle lifelike animal motion — {action_motion} (soft blinking, ear twitch, slow tail movement, calm breathing), very slow camera drift, no subject morphing, natural seamless looping"
    return VideoStoryboard(anchor=anchor, motion=motion, audio_enabled=audio_enabled, audio_prompt=audio_prompt, loop=True, duration=8)


def _build_cartoon_video(answers: dict[str, str], free_text: str | None) -> VideoStoryboard:
    char_raw = answers.get("character") or answers.get("hero") or "wizard"
    char_key = _strip_leading_emoji(char_raw.lower())
    char_desc = _lookup(_CARTOON_CHARACTER, char_key, char_key)

    action_raw = answers.get("action") or "waving"
    action_key = _strip_leading_emoji(action_raw.lower())
    action_start, action_peak = _CARTOON_ACTION.get(action_key, ("in a starting pose", "performs an expressive action"))

    setting_raw = answers.get("setting") or "in a village"
    setting_key = _strip_leading_emoji(setting_raw.lower())
    setting_desc = _lookup(_CARTOON_SETTING, setting_key, "cozy magical village")

    audio_raw = answers.get("audio") or "fun music"
    audio_key = _strip_leading_emoji(audio_raw.lower())
    audio_prompt = _lookup(_CARTOON_AUDIO, audio_key, "upbeat cheerful instrumental music, no vocals")
    audio_enabled = bool(audio_prompt)

    anchor = f"{STYLE_3D}, expressive {char_desc} character with a genuine warm smile, {action_start} in {setting_desc}, cheerful charming personality, warm golden hour glow, {LAYOUT_CENTER}, {TECHNICAL}"
    motion = f"the character {action_peak}, expressive lively movement, smooth continuous motion, gentle cinematic push-in, no hard cuts"
    return VideoStoryboard(anchor=anchor, motion=motion, audio_enabled=audio_enabled, audio_prompt=audio_prompt, loop=False, duration=8)


def _build_video_greeting(answers: dict[str, str], free_text: str | None) -> VideoStoryboard:
    occasion_raw = _strip_leading_emoji((answers.get("occasion") or "birthday").lower())
    occ_title, occ_scene, occ_default_text = _OCCASION_DATA.get(
        occasion_raw, ("Birthday", "balloons, flowers and confetti, a festive cake, warm golden glow", "Happy Birthday!")
    )

    user_text = answers.get("text") or occ_default_text

    who_val = answers.get("who") or ""
    name_suffix = _strip_leading_emoji(who_val).strip().title() if who_val.strip() else ""
    if name_suffix:
        base = user_text.rstrip("!").rstrip(",").rstrip()
        text_overlay = f"{base}, {name_suffix}!"
    else:
        text_overlay = user_text

    audio_key = _strip_leading_emoji((answers.get("audio") or "festive music").lower())
    audio_prompt = _lookup(_GREETING_AUDIO, audio_key, "upbeat warm celebratory music, festive and joyful, no vocals")
    audio_enabled = bool(audio_prompt)

    anchor = f"{STYLE_3D_SCENE_COZY}, warm {occ_title} scene, {occ_scene}, cozy festive setting, warm celebration palette with soft bokeh, {LAYOUT_TEXT}, {TECHNICAL}"
    motion = "festive ambient motion — confetti and petals gently rising then drifting down, sparkles twinkling, warm light softly pulsing, slow cinematic push-in, ends on a calm hold for the text"
    return VideoStoryboard(anchor=anchor, motion=motion, audio_enabled=audio_enabled, audio_prompt=audio_prompt, loop=False, duration=10, text_overlay=text_overlay)


# ─────────────────────────── Group C: photo-to-video ───────────────────────────
_PHOTO_MOTION_PRESET: dict[str, str] = {
    'blink': 'soft natural blinking',
    'blinking': 'soft natural blinking',
    'моргание': 'soft natural blinking',
    'head tilt': 'a gentle head tilt with a soft expression',
    'наклон головы': 'a gentle head tilt with a soft expression',
    'nod': 'a slow gentle nod',
    'кивок': 'a slow gentle nod',
    'warm smile': 'a warm gentle smile slowly appears',
    'тёплая улыбка': 'a warm gentle smile slowly appears',
    'yawn': 'a slow cozy yawn',
    'зевок': 'a slow cozy yawn',
    'wagging tail': 'softly wagging tail, ears gently perking up',
    'виляет хвостом': 'softly wagging tail, ears gently perking up',
    'gentle life': 'subtle lifelike micro-movements, breathing visible',
    'light animation': 'subtle lifelike micro-movements, breathing visible',
    'лёгкое оживление': 'subtle lifelike micro-movements, breathing visible',
}
_PHOTO_INTENSITY: dict[str, str] = {
    'barely': 'very subtle',
    'barely noticeable': 'very subtle',
    'еле заметно': 'very subtle',
    'soft': 'soft and natural',
    'мягко': 'soft and natural',
    'expressive': 'clearly expressive but smooth',
    'выразительно': 'clearly expressive but smooth',
}


def build_photo_motion_prompt(answers: dict[str, str]) -> str:
    """Build motion prompt for Group C photo-to-video tiles (no anchor needed)."""
    motion_raw = answers.get("motion") or "gentle life"
    motion_key = _strip_leading_emoji(motion_raw.lower())
    motion_desc = _lookup(_PHOTO_MOTION_PRESET, motion_key, "subtle lifelike micro-movements")

    intensity_raw = answers.get("intensity") or "soft"
    intensity_key = _strip_leading_emoji(intensity_raw.lower())
    intensity_desc = _lookup(_PHOTO_INTENSITY, intensity_key, "soft and natural")

    audio_raw = answers.get("audio") or "silence"
    audio_key = _strip_leading_emoji(audio_raw.lower())
    if "голос" in audio_key or "pet" in audio_key:
        audio_prompt = "a soft gentle pet sound (a meow or a bark)"
        audio_enabled = True
    elif "музык" in audio_key or "music" in audio_key:
        audio_prompt = "calm gentle background music, no vocals"
        audio_enabled = True
    else:
        audio_prompt = ""
        audio_enabled = False

    prompt = f"{motion_desc}, {intensity_desc}, subtle natural movement, gentle parallax, keep the original photo look, no distortion of the face"
    if audio_enabled and audio_prompt:
        prompt += f". Audio: {audio_prompt}"
    return prompt


# ─────────────────────────── Free-text (LLM) builder ───────────────────────────
_STYLE_3D_INLINE = "vibrant 3D cartoon render, modern animated feature film look, big expressive friendly eyes, soft rounded chunky shapes, smooth glossy surfaces with subtle texture, bold warm saturated colors, cheerful charming character design, bright modern animation quality"
_STYLE_COZY_INLINE = "cozy stylized 3D cartoon render, modern animated feature film look, soft rounded chunky toy-like shapes, charming miniature diorama aesthetic, richly detailed tactile materials with visible texture, warm naturalistic saturated colors, lush detailed environment, no people, no characters, inviting heartwarming atmosphere"
_STYLE_EPIC_INLINE = "epic stylized 3D cartoon landscape render, modern animated feature film look, bold saturated colors, dramatic depth and scale, lush detailed environment, no people, no characters, majestic cinematic atmosphere"
_TECHNICAL_INLINE = "Technical: warm cinematic lighting with soft rim light and gentle sun rays, soft natural shadows, glossy smooth cartoon materials with rich surface texture (not flat, not matte plastic), ray-traced GI, high quality 3D render, 8k resolution, crisp sharp details, clean anti-aliased edges, soft depth of field with gentle background bokeh"

_FREE_TEXT_SYSTEM = f"""You are a video prompt builder for Toontoon, a 3D cartoon animation platform.
The user describes a video they want (input may be in Russian or any language).
Categorize and build a text-to-video prompt using our branded 3D cartoon style.

Categories:
- "scene": landscape, nature, environment without main character
- "living": character, animal, person, creature (named or implied)
- "greeting": celebration + person + optional message text

Style blocks to copy verbatim into "anchor":
For living subjects: "{_STYLE_3D_INLINE}"
For cosy scenes: "{_STYLE_COZY_INLINE}"
For epic/vast scenes: "{_STYLE_EPIC_INLINE}"
End every anchor with: "{_TECHNICAL_INLINE}"

Rules:
- Start the anchor field with the appropriate style block (copy verbatim), then add the scene description, then TECHNICAL.
- If user mentions a person/animal/character: MUST appear as the main subject. Translate from Russian to English.
- For living subjects include: facial expression, body pose, color palette.
- Contradictory input → charming whimsical fantasy in 3D style. Never ask for clarification.
- COPYRIGHT: If a copyrighted character is named, describe it with a GENERIC paraphrase of its
  look and NEVER write the brand name. NEVER swap it for a different named brand/character.
- Video style is ALWAYS 3D cartoon regardless of what user asks.
- mode: "kinemagraph" for peaceful scenes, animals at rest; "keyframes" for actions, characters, celebrations.
- loop: true for kinemagraph; false for keyframes.
- audio: scene → ambient nature; living → cute light music; greeting → celebratory music.

Output ONLY valid JSON (no markdown, no preamble):
{{
  "category": "scene|living|greeting",
  "mode": "kinemagraph|keyframes",
  "loop": true,
  "durationSec": 8,
  "anchor": "<style block verbatim + scene description + TECHNICAL verbatim>",
  "motion": "<movement description + camera behavior + atmosphere>",
  "audio": {{"enabled": true, "prompt": "<English sound description>"}},
  "textOverlay": null,
  "negative": "{NEGATIVE}"
}}
"""


def _fallback_freetext(text: str) -> VideoStoryboard:
    """Fallback storyboard when LLM is unavailable."""
    anchor = f"{STYLE_3D_SCENE_COZY}, beautiful serene scene inspired by: {text[:80]}, warm inviting mood, {LAYOUT_CENTER}, {TECHNICAL}"
    return VideoStoryboard(
        anchor=anchor,
        motion=_FALLBACK_MOTION,
        audio_enabled=True,
        audio_prompt="calm gentle ambient music, soft and soothing, no vocals",
        loop=True,
        duration=8,
    )


async def _build_freetext_storyboard(text: str, style: str | None) -> VideoStoryboard:
    """Use LLM to categorise free-text and build anchor+motion JSON (v2)."""
    if not settings.openai_enabled:
        logger.warning("OpenAI not enabled — using fallback for free-text video")
        return _fallback_freetext(text)

    user_msg = f"User request: {text}"
    if style:
        user_msg += f"\nRequested style: {style}"

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.openai_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": settings.openai_model,
                    "messages": [
                        {"role": "system", "content": _FREE_TEXT_SYSTEM},
                        {"role": "user", "content": user_msg},
                    ],
                    "max_tokens": 600,
                    "temperature": 0.4,
                    "response_format": {"type": "json_object"},
                },
            )
            resp.raise_for_status()
            raw = resp.json()["choices"][0]["message"]["content"].strip()
            data = json.loads(raw)
    except Exception as exc:
        logger.warning("LLM free-text video build failed: %s", exc)
        return _fallback_freetext(text)

    audio = data.get("audio", {})
    return VideoStoryboard(
        anchor=data.get("anchor", ""),
        motion=data.get("motion", _FALLBACK_MOTION),
        audio_enabled=audio.get("enabled", False),
        audio_prompt=audio.get("prompt", ""),
        negative=data.get("negative", NEGATIVE),
        loop=data.get("loop", True),
        duration=int(data.get("durationSec", 8)),
        text_overlay=data.get("textOverlay"),
    )


_TILE_BUILDERS = {
    "living_nature": _build_nature_video,
    "nature_video": _build_nature_video,
    "morning_video": _build_morning_video,
    "inspiring_video": _build_inspiring_video,
    "cute_animal_video": _build_cute_animal_video,
    "cartoon_character_video": _build_cartoon_video,
    "video_greeting": _build_video_greeting,
}


async def build_storyboard(tile_id: str | None, answers: dict[str, str], free_text: str | None, style: str | None) -> VideoStoryboard:
    """Build a VideoStoryboard for Seedance text-to-video.

    Tile-driven → deterministic builder from v2 templates.
    Free-text (no tile) → LLM categorises and assembles.
    Photo tiles (C) → caller uses build_photo_motion_prompt() + first_frame_url.
    """
    if tile_id and tile_id in _TILE_BUILDERS:
        board = _TILE_BUILDERS[tile_id](answers, free_text)
        logger.info("Built v2 storyboard for tile=%s loop=%s dur=%s", tile_id, board.loop, board.duration)
        return board

    combined = free_text or ""
    if not combined and answers:
        combined = " ".join(answers.values())
    combined = prompt_style.neutralize_ip(combined) or ""
    board = await _build_freetext_storyboard(combined, style)
    logger.info("Built v2 free-text storyboard category=freetext loop=%s dur=%s", board.loop, board.duration)
    return board
