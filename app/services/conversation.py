"""Чего в разговоре не хватает и о чём спрашивать следующим.

Свободный чат задавал тот вопрос, который придумает модель. На «постер в стиле
аниме, бело-сине-красный, как для NBA» она спрашивала про персонажа, ни разу не
спросила про пропорции, второй раз возвращалась к стилю — и ни словом про
фотографию человека, хотя он её уже присылал раньше и в этот раз забыл. Кадр
вышел лесной дорогой: ни постера, ни человека, ни аниме.

Причина не в формулировке промпта. Вопрос — это решение о том, чего не хватает,
а модель этого не знала: ей не говорили ни что уже сказано, ни что у человека
есть снимок. Поэтому решение здесь, кодом, а формулировка остаётся модели:
проверять можно первое, второе на глаз и так видно.

Порядок поэтому не «сначала приятное», а по влиянию на кадр. Снимок первым:
без него в постере будет кто-то другой, и никакая правка стиля этого не
исправит. Дальше пропорции: 16:9 меняет композицию целиком, а не подрезает края.

Отсюда же порядок берёт ведомый разговор в приложении — он спрашивает план
(`GET /api/guided/plan`) и раскладывает по нему свои вопросы. Пока это были две
копии, разъехаться они могли молча: свободный чат спрашивал бы одно, соседний
экран другое, и оба считали бы себя правыми. Копия в приложении осталась
запасом на первый запуск без сети.
"""
from __future__ import annotations

# Что человек делает. Угадывается по первым словам — ошибка стоит дёшево:
# портрет самый частый случай, и поправить его можно любым следующим ответом.
INTENT_MARKS: dict[str, tuple[str, ...]] = {
    "poster": ("poster", "плакат", "постер", "cover", "обложк", "banner", "баннер"),
    "card": ("card", "открытк", "greeting", "birthday", "поздрав", "wish"),
    "product": ("product", "товар", "review", "обзор", "ugc", "holding", "unboxing"),
}

DEFAULT_INTENT = "portrait"

# Порядок вопросов внутри назначения. `photo` — не слот разбора, а вопрос про
# снимок человека, и он всегда первый: остальное описывает кадр, а он решает,
# кто в этом кадре.
ORDER: dict[str, tuple[str, ...]] = {
    "portrait": ("photo", "technique", "place", "light", "wardrobe", "framing", "format", "reference"),
    "poster": ("photo", "format", "technique", "text", "palette", "place", "reference"),
    "card": ("photo", "occasion", "text", "technique", "place", "reference"),
    "product": ("photo", "product", "place", "light", "framing", "reference"),
}

# Назначения, где без снимка обещание не выполняется вовсе: портрет и обзор
# товара — это «ты в кадре», а не «картинка на тему».
NEEDS_PHOTO = ("portrait", "product")

# Чего именно спросить — словами, а не именем поля: формулировку пишет модель,
# и ей нужен смысл вопроса, а не идентификатор.
ASK_ABOUT: dict[str, str] = {
    "photo": ("whether they want themselves in the picture, and if so ask them to attach "
              "their photo — they have used their own photo here before"),
    "format": "the shape of the frame: tall, square or wide",
    "technique": "how it should look: a photograph, a cartoon, anime, cinematic",
    "text": "what words should be printed on it, if any",
    "palette": "which colours it should use",
    "place": "where the scene happens",
    "light": "what lights the scene",
    "wardrobe": "what they are wearing",
    "framing": "how close the camera should be",
    "occasion": "what the card is for",
    "product": "what the product is and how it should appear",
    "reference": "what it should feel like — a genre, a campaign, a magazine",
}


def detect_intent(text: str) -> str:
    """Что человек делает, по его же словам."""
    low = text.lower()
    for intent, marks in INTENT_MARKS.items():
        if any(mark in low for mark in marks):
            return intent
    return DEFAULT_INTENT


def slots_for(intent: str) -> list[str]:
    """Поля разбора для назначения — без `photo`, он не разбирается из речи."""
    return [field for field in ORDER.get(intent, ORDER[DEFAULT_INTENT]) if field != "photo"]


def next_gap(
    known: dict[str, str],
    *,
    intent: str,
    photo_attached: bool,
    photo_on_file: bool,
    asked: tuple[str, ...] | list[str] = (),
) -> str | None:
    """О чём спросить следующим, или `None`, если спрашивать больше не о чем.

    Про снимок спрашиваем, когда его нет сейчас и он либо обязателен по
    назначению, либо человек уже присылал свой раньше. Второе — не догадка о
    его желании, а напоминание о том, что он делал сам: забыть приложить
    фотографию легко, а увидеть это можно только на готовом кадре, за который
    уже списано.

    Про то, что человек уже сказал, не спрашиваем никогда. Это и есть главная
    ошибка разговора: она читается как «тебя не слушали», и следующую фразу
    человек пишет уже раздражённым.

    `asked` — о чём уже спрашивали в этом разговоре. Каждое поле спрашивается
    один раз, ответили на него или нет. Иначе отказ запирает разговор насмерть:
    «без надписи» — это ответ, но слот от него не заполняется, и вопрос про
    надпись приходил бы снова и снова, пока человек не уйдёт.
    """
    order = ORDER.get(intent, ORDER[DEFAULT_INTENT])
    already = set(asked)
    for field in order:
        if field in already:
            continue
        if field == "photo":
            # Снимок есть в истории — не спрашиваем: сервер подставит тот, что
            # человек уже использовал, и скажет об этом. Вопрос «приложите
            # фотографию» и три касания выбора — самый дорогой шаг в этом пути,
            # а ответ на него мы и так знаем.
            if not photo_attached and not photo_on_file and intent in NEEDS_PHOTO:
                return "photo"
            continue
        if not known.get(field):
            return field
    return None


# Веса — по влиянию на результат, и они у каждого назначения свои: в портрете
# формат стоит два процента, в постере двадцать.
#
# Сумма вместе со снимком — ровно сто. Это не эстетика: полоса готовности
# обещает «вот столько осталось», и обещание должно сходиться. У постера
# выходило сто десять, и полоса уезжала за свой край.
#
# Числа не выдуманы. Снимок весит больше всех: без него кадр вообще не про
# этого человека. Техника решает, станет ли модель стилизовать вовсе. Формат и
# кадрирование правятся одной кнопкой после результата и потому стоят дёшево.
WEIGHTS: dict[str, dict[str, int]] = {
    "portrait": {"photo": 30, "technique": 25, "place": 20, "light": 12,
                 "wardrobe": 5, "framing": 3, "format": 2, "reference": 3},
    "poster": {"photo": 30, "format": 20, "technique": 18, "text": 18,
               "palette": 8, "place": 3, "reference": 3},
    "card": {"photo": 30, "occasion": 24, "text": 24, "technique": 14,
             "place": 5, "reference": 3},
    "product": {"photo": 35, "product": 28, "place": 14, "light": 14,
                "framing": 5, "reference": 4},
}

# Сколько снимков нужно, чтобы обещание выполнилось. У товара их два: человек и
# предмет. С одним получится обычный портрет — то есть не то, зачем пришли.
PHOTOS_NEEDED: dict[str, int] = {"product": 2}


def photos_needed(intent: str) -> int:
    return PHOTOS_NEEDED.get(intent, 1)


# Сколько веса должна набрать просьба, чтобы за неё можно было браться.
#
# Полсотни — это снимок и одно тяжёлое поле, либо два тяжёлых без снимка. Ниже
# начинается угадывание: «хочу постер» с фотографией набирает тридцать, и кадр
# по нему выйдет каким угодно.
#
# Порог, а не список обязательных полей, потому что поля разного веса и
# заменяют друг друга: сказанные техника и цвета стоят больше, чем формат,
# и требовать формат от того, кто описал остальное, — вопрос ради вопроса.
READY_WEIGHT = 50


def covered_weight(known: dict[str, str], *, intent: str, has_photo: bool) -> int:
    """Насколько описана просьба — теми же весами, что и полоса готовности."""
    weights = WEIGHTS.get(intent, WEIGHTS[DEFAULT_INTENT])
    total = sum(weights.get(field, 0) for field, value in known.items() if value)
    if has_photo:
        total += weights.get("photo", 0)
    return total


def is_ready(
    known: dict[str, str],
    *,
    intent: str,
    has_photo: bool,
    asked: list[str] | None = None,
) -> bool:
    """Можно ли браться за кадр или сначала спросить.

    Один вопрос — потолок. Не потому, что второй бесполезен, а потому, что
    человек пришёл за картинкой, а не за анкетой: после первого уточнения
    честнее показать кадр и дать его поправить, чем выяснять всё до конца на
    словах. Поправить готовое дешевле, чем вообразить несуществующее.
    """
    if asked:
        return True
    return covered_weight(known, intent=intent, has_photo=has_photo) >= READY_WEIGHT
