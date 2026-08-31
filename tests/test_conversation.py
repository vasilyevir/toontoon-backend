"""О чём свободный чат спрашивает следующим.

Все случаи здесь — с одного прогона, где чат ушёл в лес: на «постер в стиле
аниме, бело-сине-красный, как работа для Behance, стилистика как для NBA» он
спросил про персонажа, ни разу не спросил про пропорции, второй раз вернулся к
стилю и ни словом не вспомнил про фотографию человека, которую тот присылал
раньше и в этот раз забыл приложить. Кадром оказалась лесная дорога.

Ошибка такого рода не падает и не логируется — она читается как «меня не
слушают», и заметна только на готовой картинке, за которую уже списано.

    PYTHONPATH=. .venv/bin/python -m pytest tests -q
"""
from __future__ import annotations

import pytest

from app.services import conversation as c
from app.services import gpt


# Тот самый разговор: стиль, цвета и референс названы первой же фразой.
POSTER_SAID = {"technique": "anime", "palette": "white, blue and red",
               "reference": "a sports campaign"}


# ─── Что человек делает ──────────────────────────────────────────────────────

@pytest.mark.parametrize("text, intent", [
    ("Сделай постер в стиле аниме", "poster"),
    ("make me a poster for the wall", "poster"),
    ("открытка на день рождения", "card"),
    ("хочу обзор товара", "product"),
    ("нарисуй меня у окна", "portrait"),
    ("", "portrait"),
])
def test_intent_is_read_from_the_words(text, intent):
    """Назначение угадывается по первым словам, а не спрашивается отдельно."""
    assert c.detect_intent(text) == intent


# ─── 1. Про пропорции не спросили ────────────────────────────────────────────

def test_a_poster_is_asked_about_its_shape():
    """У постера пропорции — первый вопрос после снимка.

    16:9 меняет композицию целиком, а не подрезает края: спросить об этом после
    генерации значит предложить переделать кадр за новые деньги.
    """
    assert c.next_gap(POSTER_SAID, intent="poster",
                      photo_attached=True, photo_on_file=True) == "format"


def test_shape_is_asked_before_the_smaller_things():
    """Порядок — по влиянию на кадр, а не по удобству спрашивать."""
    order = c.ORDER["poster"]
    assert order.index("format") < order.index("palette") < order.index("reference")


# ─── 2. Про стиль спросили второй раз ────────────────────────────────────────

def test_what_was_said_is_never_asked_again():
    """Стиль назван первой фразой — второго вопроса о нём быть не может."""
    for _ in range(3):
        gap = c.next_gap(POSTER_SAID, intent="poster",
                         photo_attached=True, photo_on_file=True)
        assert gap != "technique"


def test_everything_said_closes_the_questions():
    """Когда сказано всё — вопросов больше нет, а не «ещё один на всякий»."""
    said = {slot: "сказано" for slot in c.slots_for("poster")}
    assert c.next_gap(said, intent="poster",
                      photo_attached=True, photo_on_file=True) is None


def test_the_directive_lists_what_is_settled():
    """Модели передаётся сказанное, а не запрет спрашивать.

    Запрет она читает и всё равно спрашивает; список уже известного она
    пересказывает своими словами и на том успокаивается.
    """
    directive = gpt.chat_directive(c.ASK_ABOUT["format"], POSTER_SAID)
    assert "technique: anime" in directive
    assert "never ask about any of it again" in directive
    assert "shape of the frame" in directive


def test_the_directive_stops_asking_when_nothing_is_missing():
    directive = gpt.chat_directive(None, POSTER_SAID)
    assert "Do not ask another question" in directive


# ─── 3 и 4. Про фотографию не вспомнили ──────────────────────────────────────

def test_a_forgotten_photo_is_not_asked_about_at_all():
    """Снимок есть в истории — вопроса быть не должно.

    Раньше здесь спрашивали. Но вопрос «приложите фотографию» и три касания
    выбора — самый дорогой шаг пути, а ответ мы знаем: тот же снимок, что и в
    прошлый раз. Сервер подставит его сам и скажет об этом.
    """
    assert c.next_gap(POSTER_SAID, intent="poster",
                      photo_attached=False, photo_on_file=True) == "format"


def test_the_photo_question_offers_their_own_photo():
    """Спрашивается именно «хочешь ли по своей фотографии», а не «приложи файл»."""
    assert "themselves" in c.ASK_ABOUT["photo"]
    assert "attach" in c.ASK_ABOUT["photo"]


def test_a_portrait_asks_for_the_photo_only_from_a_newcomer():
    """Портрет и обзор товара без снимка не выполняются вовсе.

    Но спрашиваем только того, у кого снимка нет вообще: остальным подставляем.
    """
    for intent in c.NEEDS_PHOTO:
        assert c.next_gap({}, intent=intent,
                          photo_attached=False, photo_on_file=False) == "photo"
        assert c.next_gap({}, intent=intent,
                          photo_attached=False, photo_on_file=True) != "photo"


def test_a_newcomer_is_not_nagged_about_a_photo_they_never_had():
    """Постер бывает и без человека. Тому, кто ни разу не присылал снимок, это
    не напоминание, а приставание."""
    assert c.next_gap({}, intent="poster",
                      photo_attached=False, photo_on_file=False) == "format"


def test_an_attached_photo_is_not_asked_for_again():
    assert c.next_gap({}, intent="portrait",
                      photo_attached=True, photo_on_file=True) == "technique"


# ─── Отказ отвечать закрывает вопрос ─────────────────────────────────────────

def test_a_question_is_asked_only_once():
    """«Без надписи» — это ответ, от которого слот не заполняется.

    Пока вопросы выбирались только по незаполненному, такой ответ запирал
    разговор: про надпись спрашивалось снова и снова, и выйти из этого можно
    было только уйдя.
    """
    said = dict(POSTER_SAID)
    gap = c.next_gap(said, intent="poster", photo_attached=True, photo_on_file=True)
    assert gap == "format"
    # Спросили, ответа нет — дальше по порядку, а не по второму кругу.
    gap = c.next_gap(said, intent="poster", photo_attached=True, photo_on_file=True,
                     asked=["format"])
    assert gap == "text"
    gap = c.next_gap(said, intent="poster", photo_attached=True, photo_on_file=True,
                     asked=["format", "text"])
    assert gap == "place"


def test_a_declined_photo_is_not_asked_about_again():
    """Отказ от снимка — тоже ответ. Повторить вопрос значит не принять его."""
    gap = c.next_gap(POSTER_SAID, intent="poster",
                     photo_attached=False, photo_on_file=True, asked=["photo"])
    assert gap == "format"


def test_questions_run_out():
    """Разговор обязан заканчиваться. Спросили обо всём — предлагаем сделать."""
    gap = c.next_gap({}, intent="poster", photo_attached=False, photo_on_file=True,
                     asked=list(c.ORDER["poster"]))
    assert gap is None


# ─── Связность с разбором ────────────────────────────────────────────────────

def test_every_field_is_a_real_slot_and_has_a_question():
    """Поле, которого нет в разборе, никогда не закроется — чат будет спрашивать
    о нём вечно, потому что ответ человека некуда положить."""
    for intent, order in c.ORDER.items():
        assert order[0] == "photo", f"{intent}: снимок обязан быть первым"
        for field in order[1:]:
            assert field in gpt.SLOT_MEANING, f"{intent}: {field!r} не разбирается"
            assert field in c.ASK_ABOUT, f"{intent}: нечего спросить про {field!r}"
    assert "photo" in c.ASK_ABOUT


# ─── План разговора для приложения ───────────────────────────────────────────

def test_weights_add_up_to_a_hundred():
    """Полоса готовности обещает «вот столько осталось».

    У постера сумма давала сто десять, и полоса уезжала за свой край. Обещание
    должно сходиться, иначе это не индикатор, а украшение.
    """
    for intent, weights in c.WEIGHTS.items():
        assert sum(weights.values()) == 100, f"{intent}: {sum(weights.values())}"


def test_every_field_has_a_weight_and_every_weight_a_field():
    """Поле без веса не двигает полосу, вес без поля двигает её ни от чего."""
    for intent, order in c.ORDER.items():
        assert set(c.WEIGHTS[intent]) == set(order), intent


def test_the_plan_covers_every_intent():
    """План отдаётся на все назначения сразу: приложение спрашивает его один
    раз за запуск, а не по разу на каждый выбор человека."""
    from app.routers.guided import IntentPlan, PlanResponse

    plan = PlanResponse(intents={
        intent: IntentPlan(fields=list(fields), weights=c.WEIGHTS[intent],
                           photos_needed=c.photos_needed(intent))
        for intent, fields in c.ORDER.items()
    })
    assert set(plan.intents) == set(c.ORDER)
    assert plan.intents["product"].photos_needed == 2
    assert plan.intents["poster"].fields[0] == "photo"
    assert plan.intents["poster"].fields[1] == "format"


# ─── Запрос без темы ─────────────────────────────────────────────────────────

def test_a_request_with_nothing_to_draw_is_refused():
    """Пустой запрос не должен доходить до кошелька.

    Так однажды и вышло: провайдер отказал по сети, приложение стёрло
    собранное, и следующее нажатие отправило пустоту. Сборщик промптов придумал
    «тёплую дружелюбную картинку», запасной исполнитель это нарисовал — человек
    получил фотографию двух деревянных яиц вместо постера, за свой TOONTOON.
    """
    from app.models.generation import GenerateRequest
    from app.routers.generate import _has_subject

    assert not _has_subject(GenerateRequest())
    assert not _has_subject(GenerateRequest(prompt="   "))
    # Одной фотографии мало: по ней непонятно, что с ней делать.
    assert not _has_subject(GenerateRequest(photo_url="/api/media/med_1"))

    assert _has_subject(GenerateRequest(prompt="постер в стиле аниме"))
    assert _has_subject(GenerateRequest(tile_id="tile_1"))
    assert _has_subject(GenerateRequest(style_id="anime_look"))


def test_the_model_is_told_the_photo_is_here():
    """Снимок приходит картинкой, а модель читает только текст.

    Без этой строки она отвечала «фотографии не вижу» человеку, который её
    только что приложил и видит в переписке.
    """
    directive = gpt.chat_directive(c.ASK_ABOUT["format"], POSTER_SAID, photo_attached=True)
    assert "attached their photo" in directive
    assert "never ask for it again" in directive.lower()
    assert "attached their photo" not in gpt.chat_directive(c.ASK_ABOUT["format"], POSTER_SAID)


# ─── Когда браться за кадр, а когда спросить ────────────────────────────────

def test_a_described_request_is_taken_as_is():
    """«Постер в стиле аниме, бело-сине-красный, как для NBA» плюс снимок.

    Тридцать за фотографию, восемнадцать за технику, восемь за цвета, три за
    референс — пятьдесят девять. Спрашивать после этого не о чем: человек всё
    сказал, и следующий вопрос читается как «тебя не слушали».
    """
    assert c.is_ready(POSTER_SAID, intent="poster", has_photo=True)


def test_two_words_are_not_a_request():
    """«Хочу постер» и фотография — это тридцать из ста.

    Кадр по такому выйдет каким угодно, и человек заплатит за угадывание.
    """
    assert not c.is_ready({}, intent="poster", has_photo=True)
    assert c.next_gap({}, intent="poster", photo_attached=True, photo_on_file=True) == "format"


def test_one_question_is_the_ceiling():
    """После первого уточнения берёмся за кадр, даже если описано не всё.

    Человек пришёл за картинкой, а не за анкетой: поправить готовое дешевле,
    чем вообразить несуществующее.
    """
    assert not c.is_ready({}, intent="poster", has_photo=True, asked=[])
    assert c.is_ready({}, intent="poster", has_photo=True, asked=["format"])


def test_the_photo_counts_towards_the_description():
    """Снимок — самая тяжёлая часть просьбы: без него кадр не про этого человека."""
    said = {"technique": "anime", "palette": "white and blue"}
    assert c.covered_weight(said, intent="poster", has_photo=True) == 56
    assert c.covered_weight(said, intent="poster", has_photo=False) == 26
    assert not c.is_ready(said, intent="poster", has_photo=False)


def test_heavy_fields_can_replace_each_other():
    """Порог, а не список обязательных полей.

    Сказанные техника и надпись стоят больше формата — требовать формат от
    того, кто описал остальное, значит спрашивать ради вопроса.
    """
    assert c.is_ready({"technique": "anime", "text": "NIKITA"},
                      intent="poster", has_photo=True)


def test_a_profile_weighs_as_much_as_an_attached_photo():
    """Профиль обеспечивает человека в кадре так же, как приложенный снимок.

    Значит и просьба считается описанной на те же тридцать процентов, и
    вопросов задаётся меньше — а вопрос про фотографию не задаётся вовсе.
    """
    said = {"technique": "anime", "palette": "white and blue"}
    assert c.is_ready(said, intent="poster", has_photo=True)
    assert not c.is_ready(said, intent="poster", has_photo=False)
    assert c.next_gap({}, intent="portrait",
                      photo_attached=False, photo_on_file=True) != "photo"


# ─── Взять лицо и спросить лицо — разные вопросы ─────────────────────────────

def test_a_poster_is_a_poster_of_the_person():
    """Постер в этом продукте — постер с человеком.

    Пока это жило одним кортежем с NEEDS_PHOTO, постер не подставлял профиль:
    человек просил «постер в стиле аниме, как для NBA», в строке ввода стоял
    его профиль, а приходил незнакомый мальчик. В базе таких — один из 48;
    остальные 42 с лицом, и все 42 из профиля.
    """
    for intent in ("portrait", "poster", "card", "product"):
        assert intent in c.ABOUT_A_PERSON, f"{intent} остался без лица"


def test_a_newcomer_is_still_not_nagged():
    """Уже, и намеренно: побеспокоить того, у кого снимка нет, — не то же
    самое, что взять тот, который есть.

    Портрет без лица не получается вовсе, постер получается — просто хуже.
    Вопрос новичку плюс три касания выбора — самый дорогой шаг пути.
    """
    assert set(c.NEEDS_PHOTO) < set(c.ABOUT_A_PERSON), "кортежи слили обратно"
    for intent in ("poster", "card"):
        assert intent not in c.NEEDS_PHOTO
        assert c.next_gap({}, intent=intent,
                          photo_attached=False, photo_on_file=False) != "photo"
