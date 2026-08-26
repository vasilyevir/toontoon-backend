"""Слова решают: правка это или новая просьба, нужны ли буквы, чем нарисовано.

Всё, что здесь проверяется, однажды сломалось молча. Человек прикладывал
аниме-постер и просил «сделай со мной в такой же стилистике, а вместо akai
напиши моё имя» — и получал свою фотографию на закатной улице: без стиля, без
букв, зато с приветливой улыбкой из референса. Ни одной ошибки в логах при этом
не было: с точки зрения кода всё отработало.

    PYTHONPATH=. .venv/bin/python -m pytest tests -q
"""
from __future__ import annotations

import pathlib
import re

import pytest

from app.routers.generate import _restates_the_picture, _wants_lettering
from app.services import conversation, gpt, prompt_style


# ─── Правка кадра или новая просьба ──────────────────────────────────────────


@pytest.mark.parametrize("text, named", [
    ("I want to see poster 16:9 in this styles", {"intent": "poster", "format": "16:9"}),
    ("хочу открытку квадратом", {"intent": "card", "format": "1:1"}),
    ("а давай теперь в аниме", {"technique": "anime"}),
    ("сделай горизонтально", {"format": "16:9"}),
])
def test_words_that_name_another_picture(text, named):
    assert _restates_the_picture(text) == named


@pytest.mark.parametrize("text", [
    "сделай фон ночным",
    "убери прямоугольник слева",
    "теплее свет",
    "add a hat",
])
def test_words_that_only_fix_this_one(text):
    # Правка не должна превращаться в новую просьбу: человек доволен кадром и
    # меняет в нём одно, а пересборка с нуля выбросит всё остальное.
    assert _restates_the_picture(text) == {}


def test_plain_phrase_names_no_intent():
    # `detect_intent` всегда отвечает «портрет» — это умолчание, а не ответ.
    # Умолчание не должно перебивать назначение, выбранное минуту назад.
    assert conversation.explicit_intent("сделай фон ночным") is None
    assert conversation.detect_intent("сделай фон ночным") == "portrait"
    assert conversation.explicit_intent("хочу постер") == "poster"


# ─── Буквы ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("text", [
    "а вместо akai напиши мое имя Ilya",
    "нужна надпись сверху",
    "write my name on it",
    "добавь заголовок",
])
def test_asking_for_letters(text):
    assert _wants_lettering(text)


@pytest.mark.parametrize("text", [
    "сделай меня на фоне гор",
    "теплее свет и ближе кадр",
])
def test_not_asking_for_letters(text):
    assert not _wants_lettering(text)


def test_poster_without_words_invents_nothing():
    # Постер без сказанных слов однажды набрал плакатным шрифтом саму просьбу
    # человека: «I WANT TO SEE POSTER 16:9 IN THIS STYLES».
    system = gpt._system_for(editing=True, lettering=False, poster=True)
    assert "do NOT invent any lettering" in system
    assert "MUST appear in the image" not in system


def test_poster_with_words_demands_them():
    system = gpt._system_for(editing=True, lettering=True, poster=True)
    assert "MUST appear in the image" in system


def test_letters_without_a_poster_do_not_bring_poster_layout():
    # «Напиши здесь моё имя» — заказ надписи, а не плаката: композицию человек
    # уже показал образцом.
    system = gpt._system_for(editing=True, lettering=True, poster=False)
    assert "MUST appear in the image" in system
    assert "flat graphic shapes" not in system


# ─── Чем это нарисовано ──────────────────────────────────────────────────────


def test_redraw_names_the_medium_first():
    clause = prompt_style.identity_clause(subject="person", drawn=True,
                                          medium=prompt_style.medium_of("anime"))
    # «Redrawn as a character in this style» начиналось ссылкой на стиль,
    # который в промпте ещё не назван: якорь стоит ниже. Модель к этому моменту
    # уже решила, что правит фотографию.
    assert clause.startswith("redraw this as an anime illustration, never a photograph")


def test_photographic_path_keeps_its_own_clause():
    clause = prompt_style.identity_clause(subject="person", drawn=False)
    assert clause.startswith("keep the same person")
    assert "redraw this as" not in clause


def test_editor_is_told_it_is_a_redraw_not_a_retouch():
    drawn = gpt._system_for(editing=True, lettering=False, drawn=True)
    photo = gpt._system_for(editing=True, lettering=False, drawn=False)
    assert "NEW drawing of this person" in drawn
    assert "NEW drawing of this person" not in photo


# ─── Выражение лица ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("clause", [
    prompt_style.IDENTITY_CLAUSE,
    prompt_style.DRAWN_IDENTITY_CLAUSE,
    prompt_style.cast_clause(["Igor", "Anya"]),
])
def test_expression_comes_from_the_scene(clause):
    # Набор снимков почти всегда начинается с улыбки — так люди фотографируются.
    # Без этой оговорки человек улыбался и под дождём, и на драматичном постере.
    assert "do not copy the smile" in clause


# ─── Рисунок, вернувшийся фотографией ────────────────────────────────────────


class FakeResult:
    def __init__(self, data: bytes, cost_usd: float | None = None):
        self.data = data
        self.provider_id = "p"
        self.model = "m"
        self.cost_usd = cost_usd


@pytest.fixture
def guard(monkeypatch):
    """Подмена зрения и исполнителя: проверяем решение, а не сеть."""
    from app.services import image_job as job
    from app.services.generation import operations

    calls: dict = {"runs": []}

    def vision(verdicts):
        answers = list(verdicts)

        async def _look(data):
            return answers.pop(0) if answers else False

        monkeypatch.setattr(job.gpt_service, "looks_photographic", _look)

    async def _run(db, request, prefer=None):
        calls["runs"].append(request.prompt)
        return FakeResult(b"second", cost_usd=0.04)

    monkeypatch.setattr(job.generation_core, "run", _run)
    request = operations.GenerationRequest(
        operation=operations.Operation.IMAGE_TO_IMAGE, prompt="anime poster",
    )
    return vision, calls, request


async def test_a_photograph_is_drawn_again(guard):
    from app.services.image_job import redraw_if_photographic as _redraw_if_photographic

    vision, calls, request = guard
    vision([True])
    result, prompt, redrawn = await _redraw_if_photographic(
        None, request, FakeResult(b"first", cost_usd=0.04), "anime poster", prefer=None)
    # Переделали, и на повтор ушло прямое «прошлый кадр вернулся фотографией»:
    # вежливое описание стиля модель уже прочитала и не послушалась.
    assert calls["runs"] and "previous attempt came back as a photograph" in calls["runs"][0]
    assert result.data == b"second"
    assert prompt.endswith(prompt_style.REDRAW_HARDER)
    # Повтор обязан оставить след: человек платит один раз, мы — дважды, и без
    # этого признака цену упрямства модели не сосчитать.
    assert redrawn
    # И заплачено дважды: первый кадр выброшен, но счёт за него выставлен.
    assert result.cost_usd == pytest.approx(0.08)


async def test_a_drawing_is_left_alone(guard):
    from app.services.image_job import redraw_if_photographic as _redraw_if_photographic

    vision, calls, request = guard
    vision([False])
    result, prompt, redrawn = await _redraw_if_photographic(
        None, request, FakeResult(b"first"), "anime poster", prefer=None)
    # Лишний повтор — это наши деньги и чужое ожидание.
    assert calls["runs"] == []
    assert result.data == b"first"
    assert prompt == "anime poster"
    assert not redrawn


async def test_a_failed_retry_still_returns_a_picture(monkeypatch, guard):
    from app.services import image_job as job
    from app.services.image_job import redraw_if_photographic as _redraw_if_photographic

    vision, _, request = guard
    vision([True])

    async def _boom(db, request, prefer=None):
        raise RuntimeError("провайдер лёг")

    monkeypatch.setattr(job.generation_core, "run", _boom)
    result, prompt, redrawn = await _redraw_if_photographic(
        None, request, FakeResult(b"first"), "anime poster", prefer=None)
    # Человек заплатил и ждёт картинку: неудачный повтор не повод отдать ничего.
    assert result.data == b"first"
    assert prompt == "anime poster"
    # Повтор был, пусть и неудачный: за первый запрос уже заплачено.
    assert redrawn


# ─── Кого рисуем, когда сцена описывает другого ──────────────────────────────


@pytest.mark.parametrize("clause", [
    prompt_style.IDENTITY_CLAUSE,
    prompt_style.DRAWN_IDENTITY_CLAUSE,
])
def test_scene_does_not_restyle_the_person(clause):
    # Тексты стилей писались по примерам, и в них попали юбка, укладка и
    # макияж. Человек получал своё лицо на чужом теле — с длинными волосами и
    # в платье. Личность старше сцены.
    assert "gender presentation, body proportions, height, build, hair length and" in clause
    # Борода — часть человека, а не деталь сцены: на первом же живом кадре
    # «Restaurant Exit» она пришла короче, чем у него.
    assert "a full beard stays full" in clause
    # Длину волос однажды уже теряли: её убрали заодно с шапками, и «Cartoon
    # Me» стал отращивать человеку кудри до плеч. Шапку надевает сцена, а длина
    # волос принадлежит человеку.
    assert "short hair stays short" in clause
    assert "never restyle the person to fit the description" in clause


@pytest.mark.parametrize("clause", [
    prompt_style.IDENTITY_CLAUSE,
    prompt_style.DRAWN_IDENTITY_CLAUSE,
])
def test_accessories_come_from_the_scene(clause):
    # Половина набора может быть в одной шапке — люди так и снимают себя зимой.
    # Модель читает повторяющуюся вещь как часть внешности и надевает её всюду,
    # вплоть до студийного портрета.
    assert "hats, caps, beanies, glasses, headphones, scarves" in clause
    assert "leave the head uncovered" in clause


def test_catalog_styles_do_not_dictate_gender():
    """Гардероб в витрине не решает, кто человек."""
    import pathlib

    # Список рос по мере находок, пока каждая строка здесь означала уже
    # случившуюся ошибку. 25 августа 2026 по всем 90 стилям — и в файлах, и в
    # базе, откуда витрина их и берёт, — прошли шире этого списка: телосложение,
    # волосы, макияж, одежда, местоимения, возраст. Находок ноль.
    #
    # Поэтому список теперь не журнал находок, а ограда: он держит то, что уже
    # чисто. Новый стиль с «her flowing dress» не доедет до витрины.
    banned = (
        # фигура и рост
        "slender", "slim", "petite", "curvy", "willowy", "lithe", "statuesque",
        "muscular", "toned", "hourglass",
        # волосы
        "long hair", "short hair", "flowing hair", "wavy hair", "curls",
        "ponytail", "braid", "finger waves", "tousled hair",
        # лицо
        "makeup", "make-up", "lipstick", "lashes", "eyeliner", "eyebrows",
        "winged eyeliner", "matte lip", "glossy lip", "blush", "complexion",
        # одежда, привязанная к полу
        "dress", "gown", "skirt", "blouse", "heels", "stiletto", "stilettos",
        "crop top", "cropped top", "bodice", "corset", "lingerie",
        # растительность на лице
        "beard", "stubble", "moustache", "mustache",
        # местоимения и роли
        "she", "her", "hers", "his", "him", "woman", "women", "girl", "boy",
        "lady", "gentleman", "male", "female",
        # возраст
        "young", "youthful", "middle-aged", "elderly", "teenage",
    )
    # Границы слова обязательны: «dressed head to toe in matte black» — это про
    # одежду вообще, а не про платье, и запрещать его не за что.
    pattern = re.compile(r"\b(" + "|".join(banned) + r")\b")
    checked = 0
    for path in pathlib.Path("content/styles").glob("*/*/prompt.md"):
        # Питомцам всё это не грозит: у них другой промпт и другой предмет.
        if "pet_" in str(path):
            continue
        text = path.read_text().split("---", 1)[-1].lower()
        found = sorted({m.group(0) for m in pattern.finditer(text)})
        assert not found, f"{path.parent.name}: {found}"
        checked += 1
    # Иначе тест зеленеет и на пустой папке: не найдя ни одного файла, цикл не
    # выполнится ни разу, и «всё чисто» будет означать «мы не смотрели».
    assert checked >= 80, f"стилей просмотрено всего {checked} — витрина потерялась"


# ─── Когда переделывать кадр, а когда не трогать ─────────────────────────────


class FakeStyleRow:
    def __init__(self, anchor=None):
        self.prompt_template = {"anchor": anchor} if anchor else {"text": "..."}


def test_catalog_photo_style_is_not_checked_for_drawing():
    from app.routers.generate import _wants_drawing

    # Стиль каталога без якоря — фотография. Проверять её на «не рисунок ли» и
    # переделывать значит делать каждый кадр витрины дважды.
    assert not _wants_drawing(None, FakeStyleRow(), editing=True)


def test_catalog_drawn_style_is_checked():
    from app.routers.generate import _wants_drawing

    assert _wants_drawing(None, FakeStyleRow("semi_real_3d"), editing=True)


def test_free_request_without_a_style_word_is_treated_as_drawing():
    from app.routers.generate import _wants_drawing

    assert _wants_drawing(None, None, editing=True)
    assert not _wants_drawing("realistic", None, editing=True)


# ─── Сколько снимков едет в кадр ─────────────────────────────────────────────


def test_drawn_request_goes_with_one_reference():
    from app.routers.generate import _reference_take

    assert _reference_take("anime") == 1
    assert _reference_take(None) == 1


def test_photographic_request_goes_with_three():
    from app.routers.generate import _reference_take
    from app.config import settings

    assert _reference_take("realistic") == settings.profile_reference_count


def test_catalog_photo_style_also_goes_with_three():
    from app.routers.generate import _reference_take
    from app.config import settings

    # У стиля каталога поле `style` пустое, а техника лежит в его шаблоне. Пока
    # «пусто» значило «рисунок», витрина уезжала с одним референсом — то есть с
    # худшим сходством, чем та же просьба словами.
    assert _reference_take(None, FakeStyleRow()) == settings.profile_reference_count
    assert _reference_take(None, FakeStyleRow("anime")) == 1


# ─── Куда уходит постер ──────────────────────────────────────────────────────


def test_lettering_routes_by_words_not_by_intent():
    """Постер без слов не должен ехать к типографу.

    Замер 24 августа: «сделай мне постер в аниме» без единого названного слова
    вернулся с надписью «CITY HORIZON SUNSET PROTOCOL». Мы сами отправили
    пустой постер к лучшему в наборе типографу и попросили не набирать букв.
    """
    from app.routers import generate as router

    # Маршрут остался у слов и у старых сборок, которые шлют назначение в поле
    # стиля. Само по себе назначение «постер» больше никого никуда не уводит:
    # в цепочке предпочтений его нет.
    source = pathlib.Path(router.__file__).read_text()
    chain = source[source.index("prefer_used = ("):source.index("image_job.schedule(")]
    assert "preferred_provider(style)" in chain
    assert "preferred_provider(intent)" not in chain
    assert prompt_style.preferred_provider("poster") == prompt_style.LETTERING_PROVIDER


# ─── Образец стиля владеет внешним видом ─────────────────────────────────────


def test_style_sample_silences_our_own_look_words():
    """Приложен образец — наши прилагательные про вид уходят из промпта.

    Человек приложил плоский графичный постер и попросил «сделай меня в такой
    же стилистике». Зрение прочитало образец как `anime`, и в промпт уехал наш
    якорь: «warm hand-painted backgrounds with nostalgic pastoral mood». Модель
    послушала слова, а не картинку, и вернула лес с белкой.

    Слова спорят с картинкой только тогда, когда картинка уже есть. Поэтому
    глушится это ровно при образце, а не вообще.
    """
    with_sample = prompt_style.assemble(
        "the person in the frame", style_key="anime", is_text=False,
        editing=True, style_ref=True)
    assert "nostalgic pastoral" not in with_sample
    assert "warm hand-painted backgrounds" not in with_sample
    # Свет и фон — тоже собственность образца.
    assert "soft cinematic lighting" not in with_sample
    assert "detailed background" not in with_sample
    # А сам образец по-прежнему назван, иначе модель перенесёт из него людей.
    assert "STYLE SAMPLE" in with_sample

    without = prompt_style.assemble(
        "the person in the frame", style_key="anime", is_text=False, editing=True)
    assert "nostalgic pastoral" in without


def test_medium_comes_from_the_sample_not_from_our_guess():
    """Носитель при образце не называется словом.

    Тот же постер зрение во второй раз прочитало как `semi_real_3d`, и промпт
    попросил «stylised 3D render» — у модели при этом перед глазами лежал
    плоский рисунок. Наша догадка о технике не должна спорить с образцом.
    """
    guessed = prompt_style.identity_clause(
        subject="person", drawn=True, medium="a stylised 3D render", from_sample=True)
    assert "stylised 3D render" not in guessed
    assert "style of the attached style sample" in guessed

    # Без образца носитель называется как прежде: догадок там нет, есть выбор.
    named = prompt_style.identity_clause(
        subject="person", drawn=True, medium="a stylised 3D render")
    assert "redraw this as a stylised 3D render" in named


def test_the_brand_guard_spares_our_own_sample_phrase():
    """«in the style of» вырезается ради брендов, а не ради образца.

    Фраза стоит в списке потому, что за ней обычно следует студия. Но с
    приложенным образцом её пишет наш же сборщик — и вырезание оставляло
    «Redraw the character  the sample»: предлог съеден, смысл потерян.
    """
    ours = prompt_style.strip_brands("Redraw the character in the style of the sample")
    assert ours == "Redraw the character in the style of the sample"
    assert "in the style of the attached style sample" in prompt_style.strip_brands(
        "redraw this in the style of the attached style sample")

    # Бренды по-прежнему уходят вместе с фразой.
    assert "Pixar" not in prompt_style.strip_brands("a poster in the style of Pixar")
    assert "in the style of" not in prompt_style.strip_brands("in the style of Ghibli warmth")


async def test_the_retry_goes_to_a_different_model(monkeypatch, guard):
    """Повтор уходит не к тому, кто только что провалился.

    Замер 26 августа на одном и том же промпте: `gemini-3-pro` возвращает
    рисунок два раза из трёх, `gemini-3.1-flash` — три из трёх. Второй заход к
    тому же исполнителю с теми же словами, только строже, — это ставка на ту же
    монету, и однажды она легла так же: «постер в стиле аниме» пришёл
    фотографией дважды подряд.

    Основным pro при этом остаётся: он единственный надёжно набирает буквы, а
    постер без надписи — не постер.
    """
    from app.services import image_job as job  # noqa: F401

    vision, calls, request = guard
    vision([True])
    seen: list[str | None] = []

    async def _run(db, request, prefer=None):
        seen.append(prefer)
        return FakeResult(b"second", cost_usd=0.04)

    monkeypatch.setattr(job.generation_core, "run", _run)
    await _redraw_if_photographic_at(job, request, prompt_style.DRAWN_PROVIDER)
    assert seen == [prompt_style.REDRAW_FALLBACK_PROVIDER]

    # А если и первый кадр делал запасной — второй раз менять не на кого.
    seen.clear()
    vision([True])
    await _redraw_if_photographic_at(job, request, prompt_style.REDRAW_FALLBACK_PROVIDER)
    assert seen == [prompt_style.REDRAW_FALLBACK_PROVIDER]


async def _redraw_if_photographic_at(job, request, prefer):
    return await job.redraw_if_photographic(
        None, request, FakeResult(b"first", cost_usd=0.04), "anime poster", prefer=prefer)
