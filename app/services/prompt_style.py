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
    "(not flat, not matte plastic), ray-traced GI, high quality 3D render, "
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
    # Полуреалистичная анимация: пропорции и кожа ближе к живым, но это
    # по-прежнему рисунок. Между "3d_cartoon" (игрушечные формы, огромные
    # глаза) и "realistic" (фотография) — и именно сюда попадает то, что люди
    # называют «как в мультфильме, только реальнее».
    # Рисунок, сделанный руками: акварель, тушь, масло, карандаш.
    #
    # Якорь намеренно скупой: он говорит только «это не фотография, у картинки
    # есть материал». Чем именно нарисовано — сказано в тексте самого стиля, и
    # спорить с ним якорь не должен.
    "painted": {
        "anchor": (
            "a hand-made artwork, not a photograph: the medium is visible in "
            "every stroke, the surface it is made on shows through, edges are "
            "drawn rather than photographed, no photographic rendering and no "
            "camera look anywhere in the frame"
        ),
        "technical": (
            "Technical: honest materials, visible texture of the medium, "
            "clean composition, high resolution"
        ),
    },
    "semi_real_3d": {
        "anchor": (
            "stylized 3D animated feature film look with lifelike proportions, "
            "softly rendered skin with visible texture and freckles, "
            "expressive but naturally sized eyes with clear catchlights, "
            "flowing detailed hair with individual strands catching the light, "
            "warm cinematic colour grading, painterly rendering, "
            "not toy-like, not plastic, not photographic"
        ),
        "technical": (
            "Technical: warm golden hour light with strong rim light, "
            "soft volumetric glow, shallow depth of field, "
            "high quality render, crisp details, clean edges"
        ),
    },
    "anime": {
        "anchor": (
            "anime illustration style, clean cel shading, expressive large eyes, "
            "vibrant yet soft color palette, detailed hand-drawn linework, "
            # Здесь стояло «Studio Ghibli inspired warmth» — бренд-фильтр вырезал
            # название и оставлял «Studio  inspired warmth». Описание вместо имени.
            "warm hand-painted backgrounds with nostalgic pastoral mood"
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

# Негатив для рисованных стилей.
#
# Общий список запрещает «flat 2D illustration, flat vector art, matte plastic
# look» — для фотографии это правильно, а для аниме и мультика это инструкция
# против самой задачи: модель получала «нарисуй» и «не рисуй» одновременно и
# выдавала фотографию. Оставлены только запреты, осмысленные и для рисунка.
NEGATIVE_DRAWN = (
    "distorted anatomy, creepy expressions, dead eyes, blank stare, "
    "messy cluttered background, small unreadable text, "
    "watermarks, signatures, cropped limbs, extra fingers or limbs, "
    "low quality, jpeg artifacts, scary dark atmosphere, "
    "dull muted colors, washed out palette, muddy dirty colors, oversaturated acidic neon, "
    "stiff lifeless pose, flat dull expression, boring symmetrical static stance, "
    "subject too small in frame, bad framing, empty wasted composition, "
    "text, letters, words, captions, watermark, signature, logo"
)


# Запрет на буквы — половина этих списков, и постеру он противопоказан.
#
# Оба набора кончаются на «text, letters, words, captions». Для портрета это
# правильно: подпись в углу кадра там всегда мусор. Для постера это отмена
# самого постера — человек просил слова, а мы их запрещаем, и ни одна модель
# после такого их не нарисует. Вырезаем запрет ровно там, где буквы и заказаны.
_LETTER_BANS = ("text, letters, words, captions, ", "small unreadable text, ")


def negative_for(style_key: str, *, lettering: bool = False) -> str:
    """Что запрещаем. `lettering` — надпись заказана и запрещать её нельзя."""
    negative = NEGATIVE_DRAWN if is_drawn(style_key) else NEGATIVE_PROMPT
    if not lettering:
        return negative
    for ban in _LETTER_BANS:
        negative = negative.replace(ban, "")
    return negative


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
    """Resolve a free-text style label to a preset key (defaults to 3d_cartoon).

    Ключ пресета принимается как есть. Таблица ниже собрана из слов, которыми
    стиль называют люди («pixar», «акварель»), и ключей в ней не было: приложение
    когда-то слало подписи с кнопок. Теперь оно шлёт `scene_cozy` и
    `semi_real_3d` — то, что вернул разбор фразы, — и всё это не находилось в
    таблице и уходило в умолчание. Выглядело это как «выбор стиля ни на что не
    влияет»: молча, на каждом кадре, кроме `anime` и `realistic`, которые
    случайно совпали с подписями.
    """
    if not style:
        return DEFAULT_STYLE
    key = style.strip().lower()
    if key in PRESETS:
        return key
    return _STYLE_MAP.get(key, DEFAULT_STYLE)


def is_text_tile(category: str | None) -> bool:
    return category in _TEXT_CATEGORIES if category else False


# Композиция постера — вместо пейзажа, который просит якорь.
POSTER_LAYOUT = (
    "flat graphic poster composition: the subject cut out against solid blocks "
    "of colour, no scenery and no landscape behind the lettering, "
    "generous margins, the type is the loudest element in the frame"
)

# Куски якорей и технических хвостов, которые заказывают фон.
#
# Якорь аниме просит «warm hand-painted backgrounds with nostalgic pastoral
# mood» — и получает пасторальный пейзаж даже там, где заказан постер: на
# готовом кадре вокруг вертикального постера оказался нарисованный лес. Стиль
# должен решать, чем нарисовано, а не что нарисовано вокруг.
_SCENIC_CLAUSES = (
    "warm hand-painted backgrounds with nostalgic pastoral mood",
    "lush detailed environment",
    "warm hand-painted backgrounds",
    "detailed background",
)


# Запрет на людей внутри «пейзажных» якорей.
#
# `scene_cozy` и `scene_epic` писались под кадры без людей — уютную комнату,
# горы, — и содержат «no people, no characters». Но в них же упирается слово
# «кинематографично» из речи человека, и якорь оказывается в промпте, весь смысл
# которого — человек в кадре. Промпт начинает спорить сам с собой: сверху
# «сохрани этого человека», ниже «людей не рисовать». Кто победит, решает модель,
# и решает по-разному.
_NO_PEOPLE_CLAUSES = ("no people, no characters, ", "no people, no characters")


def _with_people(text: str) -> str:
    for clause in _NO_PEOPLE_CLAUSES:
        text = text.replace(clause, "")
    return text.strip().strip(",").strip()


def _without_scenery(text: str) -> str:
    for clause in _SCENIC_CLAUSES:
        text = text.replace(clause, "")
    # После вырезания остаются двойные запятые и хвостовая пунктуация.
    while ", ," in text:
        text = text.replace(", ,", ",")
    return text.strip().strip(",").strip()


def assemble(scene: str, *, style_key: str, is_text: bool, editing: bool = False,
             subject: str = "person", poster: bool = False,
             style_ref: bool = False, redraw: bool = False,
             cast: list[str] | None = None) -> str:
    """Wrap a scene description with the style anchor (first) and technical (last).

    На редактировании снимка порядок другой: первым идёт требование сохранить
    человека, и только потом стиль. Модель читает промпт слева направо, а
    сходство лица — то единственное, ради чего фотографию вообще прислали;
    начинать с описания стиля значит предлагать нарисовать заново.

    ``poster`` — собирается постер. Тогда из якоря и технического хвоста
    вырезаются просьбы про фон и добавляется композиция постера: иначе стиль
    заказывает пейзаж поверх того, что человек просил заполнить буквами.

    ``cast`` — имена людей в кадре по порядку их референсов. Со списком из
    двоих и больше требование сохранить внешность заменяется на «кто есть кто»:
    сохранить нужно каждого, и главная ошибка здесь другая — не потеря
    сходства, а слипание двух лиц в одно.

    ``style_ref`` — приложен образец стиля. Про него надо сказать отдельно, и
    сказать рано: иначе модель перенесёт из образца людей и предметы вместо
    палитры и набора.
    """
    preset = PRESETS.get(style_key, PRESETS[DEFAULT_STYLE])
    scene = scene.strip().strip(".").strip()
    anchor = preset["anchor"]
    technical = preset["technical"]
    # Человек в кадре есть — значит запрет на людей из якоря вон. Признак тот
    # же, что и у требования сохранить внешность: если мы правим снимок, на нём
    # кто-то есть.
    if editing:
        anchor = _with_people(anchor)
    if poster:
        anchor, technical = _without_scenery(anchor), _without_scenery(technical)
    if not editing:
        parts = []
    elif redraw:
        # Своя прошлая работа правится, а не пересоздаётся.
        parts = [REDRAW_CLAUSE]
    elif cast and len(cast) > 1:
        parts = [cast_clause(cast, drawn=is_drawn(style_key),
                             medium=medium_of(style_key))]
        if poster:
            parts.append(CUTOUT_CLAUSE)
    else:
        parts = [identity_clause(subject=subject, drawn=is_drawn(style_key),
                                 cutout=poster, medium=medium_of(style_key))]
    if style_ref:
        parts.append(STYLE_REF_CLAUSE)
    parts += [anchor, scene]
    if poster:
        parts.append(POSTER_LAYOUT)
    if is_text:
        parts.append(LAYOUT_BLOCK)
    parts.append(technical)
    if editing and is_drawn(style_key):
        # Последнее слово — о том, что это рисунок целиком.
        #
        # Хвост промпта модель взвешивает сильнее середины, а сорваться она
        # может именно здесь: фон рисуется, лицо остаётся фотографией, и
        # получается человек, вырезанный из снимка и вклеенный в рисунок.
        parts.append(WHOLLY_DRAWN)
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

# ─── Путь с фотографией ──────────────────────────────────────────────────────

# Первое, что читает модель на редактировании снимка. Формулировка нарочно
# избыточна: у третьего поколения Kling нет ручки на лицо, и сходство держится
# только текстом, поэтому перечислено по пунктам, что именно менять нельзя.
# Что берётся с референса, а что нет.
#
# Сходство — это черты, а не мина. Референс почти всегда снят улыбающимся: люди
# так фотографируются. Модель копирует с него всё подряд, и человек получает
# одну и ту же улыбку в любой сцене — в драматичном кадре, на постере, ночью
# под дождём. Поэтому выражение и поза названы отдельно и отданы сцене: они
# часть того, что происходит в кадре, а не часть того, кто этот человек.
_EXPRESSION_CLAUSE = (
    "their expression, gaze and pose come from the scene being described, not "
    "from the reference photo — do not copy the smile or the head angle of the "
    "reference unless the scene asks for them"
)

# Что делать, когда сцена описывает человека, которым он не является.
#
# Тексты стилей писались по примерам: в них попадали и юбка, и укладка, и
# макияж. Модель читает их как описание того, кого рисовать, — и человек
# получает своё лицо на чужом теле. Личность старше сцены: одежду подгоняют под
# человека, а не человека под одежду.
_UNCHANGED_CLAUSE = (
    "their gender presentation, body proportions, height, build and hair length "
    "stay exactly as in the reference photo — short hair stays short. If the scene names clothing, hair or make-up "
    "that does not suit this person, adapt it to them — never restyle the "
    "person to fit the description"
)

# Что на снимке надето — это снимок, а не человек.
#
# В наборе половина кадров может быть в одной шапке — люди так и снимают себя
# зимой. Модель читает повторяющуюся вещь как часть внешности и надевает её
# всюду: человек просит студийный портрет и получает себя в шапке. Своей
# фразой это стоит сказать прямо: аксессуары приходят из сцены, а если сцена
# молчит — их нет.
_WARDROBE_CLAUSE = (
    "hats, caps, beanies, glasses, headphones, scarves and the clothes seen in "
    "the reference photos belong to those photos, not to this person: dress "
    "them as the scene describes, and when the scene says nothing about it, "
    "leave the head uncovered and the face unobstructed"
)

IDENTITY_CLAUSE = (
    "keep the same person from the reference photo: same face and facial features, "
    "same hairstyle and hair colour, same skin tone, same body type, same age and gender, "
    "clearly recognisable as the same individual, do not replace them with another person. "
    f"{_UNCHANGED_CLAUSE}. {_WARDROBE_CLAUSE}. {_EXPRESSION_CLAUSE}"
)

# То же для питомца. Отдельный текст, а не правка общего: «same age and gender,
# same skin tone» на кошке — это инструкция ни о чём, а модель всё равно её
# читает и тратит на неё внимание.
PET_IDENTITY_CLAUSE = (
    "keep the same animal from the reference photo: same breed and body shape, "
    "same fur colour and markings, same eye colour, same face, "
    "clearly recognisable as the same pet, do not replace it with another animal"
)


# То же требование для рисованных стилей — но это не сохранение, а перерисовка.
#
# Полного совпадения черт у нарисованного лица не бывает, и требовать «то же
# лицо, тот же тон кожи» — значит тянуть модель обратно в фотографию. Человек
# просил аниме: он должен узнать себя, а не увидеть свой снимок, обведённый по
# контуру.
#
# «Same clothing» отсюда убрано намеренно. Одежда — это часть сцены, а не часть
# того, кто человек: на постере NBA он оказывался в той же серой футболке, в
# которой сфотографировался дома, потому что мы сами это и просили. Что надето,
# решает текст сцены; если сцена молчит, редактор и так оставит как было.
DRAWN_IDENTITY_CLAUSE = (
    "the person from the reference photo, redrawn as a character in this style: "
    "recognisably them by face shape, hairstyle and hair colour and build, "
    "not somebody else. This is a drawing, not a traced photograph — stylise "
    "them fully, the linework, shading and proportions belong to the style and "
    "not to the photograph. Their clothes are part of the scene, not part of "
    "who they are. "
    f"{_UNCHANGED_CLAUSE}. {_WARDROBE_CLAUSE}. {_EXPRESSION_CLAUSE}"
)


# Что дописывается к требованию сохранить человека, когда собирается постер.
#
# Редактор по умолчанию бережёт кадр целиком — в этом смысл правки снимка. На
# постере это оборачивается тем, что комната человека остаётся на месте, а при
# переходе в 16:9 её просто дорисовывают вширь: получается та же комната,
# только шире. Про фон в требовании не было ни слова, вот его и сохраняли.
CUTOUT_CLAUSE = (
    "take ONLY the person from the reference photo: cut them out and discard "
    "the room, the walls, the furniture and everything else that was around "
    "them. The background is built from scratch out of flat colour and type. "
    "Never extend, outpaint or widen the original photograph"
)


# Что сказать про приложенный образец стиля.
#
# Без этой строки модель делает единственное, что умеет с лишней картинкой:
# переносит из неё людей и предметы. Человек прикладывал постер ради палитры и
# набора, а получал чужого баскетболиста в своём кадре.
STYLE_REF_CLAUSE = (
    "the last reference image is a STYLE SAMPLE, not a person: copy its palette, "
    "its drawing technique, its lighting and the way its layout is composed. "
    "Never copy the people, faces, objects, logos or lettering that appear in it"
)


# Когда исходник — наша же прошлая работа.
#
# Требование «сохрани лицо с фотографии» здесь неуместно вдвойне: лица на
# рисунке нет — есть его изображение, и тянуть модель к фотографической
# точности значит уводить кадр обратно в фотографию. А просить «нарисуй заново»
# значит потерять всё, что человеку в этом кадре понравилось. Он сказал
# «вот этот, но поменяй фон» — значит меняем фон.
REDRAW_CLAUSE = (
    "this picture is the person's own earlier result, not a photograph: keep "
    "its subject, its likeness and its composition exactly as they are, and "
    "change only what is asked for. Do not redraw it from scratch and do not "
    "make it more photographic"
)


ORDINALS = ("the first reference photo", "the second reference photo",
            "the third reference photo", "the fourth reference photo")


def cast_clause(names: list[str], *, drawn: bool = False,
                medium: str | None = None) -> str:
    """Кто есть кто, когда в кадре не один человек.

    Модель связывает референсы с людьми по порядку, поэтому «первый снимок —
    Никита, второй — Аня» здесь не оформление, а единственный способ не
    перепутать. Без этого получается усреднённое лицо, показанное дважды, —
    самая обидная ошибка совместного кадра: человек ждал себя с близким, а
    видит двух незнакомцев.
    """
    who = ", ".join(
        f"{ORDINALS[i] if i < len(ORDINALS) else f'reference photo {i + 1}'} is {name}"
        for i, name in enumerate(names)
    )
    look = ("redrawn but recognisably themselves"
            if drawn else "photographically themselves")
    # Та же оговорка про выражение, что и для одного человека: в совместном
    # кадре одинаковая улыбка с двух разных снимков читается ещё хуже.
    opening = (f"redraw this as {medium or 'an illustration'}, never a photograph. "
               if drawn else "")
    return (
        f"{opening}{len(names)} different people must all appear together in one picture, "
        f"each {look}: {who}. Keep every one of them their own person: never "
        "merge their faces, never draw the same person twice, never leave "
        f"anyone out. {_EXPRESSION_CLAUSE}"
    )


# Носитель, названный коротко: «аниме-иллюстрация», «3D-мультфильм».
#
# Нужен для первой строки промпта. Требование сохранить человека начиналось
# словами «redrawn as a character in this style», где «этот стиль» ещё не
# назван — якорь стоит ниже. Модель читает слева направо и к моменту, когда
# узнаёт про аниме, уже решила, что правит фотографию: результат — тот же
# снимок с подкрашенным фоном.
MEDIUM: dict[str, str] = {
    "painted": "a hand-made artwork",
    "anime": "an anime illustration",
    "3d_cartoon": "a 3D cartoon render",
    "semi_real_3d": "a stylised 3D render",
    "scene_cozy": "a cosy stylised 3D illustration",
    "scene_epic": "an epic stylised 3D illustration",
}


def medium_of(style_key: str) -> str:
    return MEDIUM.get(style_key, "an illustration")


def identity_clause(*, subject: str, drawn: bool = False, cutout: bool = False,
                    medium: str | None = None) -> str:
    """Требование сохранить того, кто на снимке.

    ``cutout`` — из снимка берётся только человек, а его окружение
    выбрасывается: на постере оно не фон, а помеха.
    """
    if subject == "pet":
        base = PET_IDENTITY_CLAUSE
    elif drawn:
        # Носитель — первым словом. «Нарисуй это аниме-иллюстрацией» модель
        # понимает сразу; «сохрани человека в этом стиле» она понимает как
        # правку фотографии, которой стиль потом припишут.
        base = (f"redraw this as {medium or 'an illustration'}, "
                f"never a photograph: {DRAWN_IDENTITY_CLAUSE}")
    else:
        base = IDENTITY_CLAUSE
    return f"{base}. {CUTOUT_CLAUSE}" if cutout else base


# Последняя строка промпта для рисованных стилей.
WHOLLY_DRAWN = (
    "every part of this picture is drawn, the face and the skin included: no "
    "photographic face, no photographed skin texture, no cut-out photograph "
    "pasted into the artwork"
)


# Что дописывается на второй заход, когда первый вернулся фотографией.
#
# Тон другой намеренно: обычные формулировки редактор уже прочитал и не
# послушался, поэтому здесь прямая констатация ошибки. На повторе это работает
# лучше, чем ещё одно вежливое описание стиля.
REDRAW_HARDER = (
    "IMPORTANT: the previous attempt came back as a photograph and was "
    "rejected. Do not reuse or retouch the reference photograph. Draw the "
    "whole picture from scratch: hand-drawn outlines, flat shading, drawn "
    "skin and drawn hair. The only thing taken from the photograph is who "
    "this person is"
)


# Стили, которые рисуют, а не снимают. Всё, кроме фотореалистичного якоря.
def is_drawn(style_key: str) -> bool:
    return style_key != "realistic"


# Запреты для фото-пути у OpenAI. Отдельный набор, потому что общий начинается
# со слов «friendly non-scary cartoon face»: на снимке живого человека это
# инструкция ровно в обратную сторону от того, что обещает экран.
PHOTO_VISUAL_GUARDS = (
    "photorealistic result, natural realistic skin texture with pores and fine detail, "
    "correct anatomy with five fingers, natural facial proportions, "
    "subject large and clear in frame, tidy uncluttered background, "
    "clean natural colour grading, "
    "NO text NO letters NO words NO watermark in the image, "
    "not a cartoon, not an illustration, not a 3D render"
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


def guards_for(*, editing: bool) -> str:
    """Какие запреты дописать в промпт: путь со снимком отличается от рисунка."""
    return PHOTO_VISUAL_GUARDS if editing else OPENAI_VISUAL_GUARDS

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


# ─── Уточнения к готовому кадру ──────────────────────────────────────────────
# Человек посмотрел на результат и говорит, что поправить. Каждое уточнение —
# фраза в конец промпта и, где нужно, предпочтительный исполнитель.
#
# Список закрытый и составлен из отказов, замеренных на ста одиннадцати кадрах:
# часть моделей молча возвращает фотографию вместо рисунка, часть под сильной
# переделкой идеализирует лицо. Это разные болезни, и лечатся они по-разному —
# первая сменой исполнителя, вторая усилением требования к сходству.
REFINEMENTS: dict[str, dict[str, str]] = {
    "more_drawn": {
        "clause": ("clearly illustrated and stylised, not photographic: visible "
                   "drawn linework and flat shaped shading"),
        # GPT Image 2 на рисованном якоре молча возвращает фотографию; Gemini
        # стилизует честно. Замерено.
        "prefer": "openrouter_gemini",
    },
    "closer_to_photo": {
        "clause": ("keep the face, hairstyle length and body shape exactly as in "
                   "the source photo, do not idealise or slim the features"),
        "prefer": "openrouter_gpt",
    },
    "wider_frame": {
        "clause": "wider framing, more space around the subject, nothing cropped at the edges",
        "prefer": "",
    },
    "different_light": {
        "clause": "a different lighting scheme from a single clearly motivated source",
        "prefer": "",
    },
}


# Сколько слов человека пускаем в промпт. Двести символов — это фраза-другая,
# больше для уточнения и не нужно, а без потолка сюда уедет всё что угодно.
NOTE_LIMIT = 200


def refine(prompt: str, key: str | None, note: str | None = None) -> tuple[str, str | None]:
    """Дописать уточнение и подсказать исполнителя.

    `key` говорит, что не так, `note` — как именно надо. Кнопка без пояснения
    остаётся угадыванием: «другой свет» не значит «какой», и модель выберет
    сама, чаще всего не то.

    Пояснение пишет человек, поэтому оно проходит те же чистки, что и любой
    пользовательский текст: чужие персонажи заменяются описанием, названия
    студий вырезаются. Без этого свободное поле стало бы дырой в обход всех
    защит, которые стоят на обычном пути.

    Возвращает промпт как есть, если уточнение неизвестно: неизвестное имя —
    повод ничего не менять, а не повод отказать человеку в генерации.
    """
    entry = REFINEMENTS.get(key or "")
    parts = [prompt]
    if entry is not None:
        parts.append(entry["clause"])
    if note and note.strip():
        cleaned = strip_brands(neutralize_ip(note.strip()[:NOTE_LIMIT]) or "")
        if cleaned:
            parts.append(cleaned)
    return ", ".join(p for p in parts if p), (entry["prefer"] or None) if entry else None


# ─── Реставрация ─────────────────────────────────────────────────────────────
# Единственный текст на всю операцию. Стиля здесь нет и быть не может: человек
# принёс единственную карточку молодой бабушки, и «улучшить» её значит вернуть
# ему похожую женщину. Поэтому промпт не собирается из полей и не переписывается
# GPT — он один и тот же всегда.
RESTORE_PROMPT = (
    "restore this damaged photograph: remove scratches, dust, creases and noise, "
    "recover natural colour where it has faded, sharpen detail that is present. "
    "Do not change faces, expressions, clothing, background or composition. "
    "Do not add anything that is not already in the frame, do not beautify, "
    "do not smooth skin, do not modernise"
)


# ─── Куда уводит назначение ──────────────────────────────────────────────────
# Постер и открытка живут надписью, а буквы умеет не всякий: 17 августа на одном
# лице с одним словом Gemini 3 Pro набрал текст верно, а GPT Image 2 —
# основной исполнитель фотопути — проигнорировал его целиком и молча, нарисовав
# вместо надписи мазки в заданной палитре.
#
# Поэтому это не подсказка, а маршрут: без него человек получит красивый кадр
# без единственного, ради чего он и пришёл.
LETTERING_PROVIDER = "openrouter_gemini_pro"

# Кому отдавать рисованный кадр.
#
# Замер 24 августа, «Cartoon Me» три раза подряд: у GPT Image 2 — ноль
# рисунков из трёх, по 75 секунд каждый (то есть с повтором, который тоже не
# помог), качество 0 из 10. У Gemini 3 Pro — три из трёх, по 25 секунд,
# качество 8. Разница не в промпте: он был один и тот же.
DRAWN_PROVIDER = "openrouter_gemini_pro"

PREFERRED_PROVIDER: dict[str, str] = {
    "poster": LETTERING_PROVIDER,
    "card": LETTERING_PROVIDER,
}


def preferred_provider(style: str | None) -> str | None:
    return PREFERRED_PROVIDER.get((style or "").strip().lower())
